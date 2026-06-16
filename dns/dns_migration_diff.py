#!/usr/bin/env python3
"""
dns_migration_diff.py — Validate a Route53 -> Cloudflare DNS migration.

Compares a `cli53 export --full` BIND file (the source of truth from AWS)
against the live Cloudflare zone, and reports:

  * MISSING    records present in cli53 but absent in Cloudflare  (the dangerous ones)
  * EXTRA      records present in Cloudflare but not in cli53
  * CHANGED    same name+type on both sides but the value set differs
  * REVIEW     cli53 lines that aren't standard BIND (Route53 ALIAS pseudo-records)
               -> these became CNAMEs/flattened apex during migration, confirm by hand

The Cloudflare side can come from either:
  --cf-bind  <file>     a BIND zone exported from the Cloudflare dashboard (no token needed)
  --cf-api              the Cloudflare API (needs --zone-id and CLOUDFLARE_API_TOKEN env)

Comparison is done on record CONFIG, not resolved DNS. That matters: a proxied
(orange-cloud) record returns Cloudflare anycast IPs over the wire, but its
configured content is still the origin/ELB hostname — which is what we compare.

Exit code is non-zero if any MISSING records are found.
"""

import argparse
import ipaddress
import os
import sys
from collections import defaultdict

import dns.rdata
import dns.rdataclass
import dns.rdatatype

# Record types we expect Cloudflare to legitimately own/rewrite, so apex copies
# of these are reported as "expected" rather than as failures.
APEX_REWRITTEN = {"SOA", "NS"}

# A reasonably broad set of standard RR types. A type token outside this set is a
# strong signal the line is a cli53 alias pseudo-record -> route to REVIEW.
KNOWN_TYPES = {
    "A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA", "SRV", "CAA",
    "PTR", "NAPTR", "DNAME", "SPF", "HINFO", "LOC", "SSHFP", "TLSA", "DS", "CERT",
}
CLASSES = {"IN", "CH", "HS", "CS"}


# --------------------------------------------------------------------------- #
# BIND master-file tokenizer (quote- and paren-aware)
# --------------------------------------------------------------------------- #
def logical_lines(text):
    """Yield (raw, tokens) for each logical record line.

    Handles: ';' comments (ignored outside quotes), '( ... )' continuation
    across physical lines, and double-quoted strings kept as single tokens.
    """
    buf_raw = []
    tokens = []
    paren_depth = 0

    def flush():
        nonlocal tokens, buf_raw
        if tokens:
            out = (" ".join(buf_raw).strip(), tokens)
            tokens, buf_raw = [], []
            return out
        buf_raw = []
        return None

    for physical in text.splitlines():
        buf_raw.append(physical)
        i, n = 0, len(physical)
        in_quote = False
        cur = ""
        while i < n:
            c = physical[i]
            if in_quote:
                if c == '"':
                    in_quote = False
                    tokens.append('"' + cur + '"')
                    cur = ""
                else:
                    cur += c
                i += 1
                continue
            if c == '"':
                in_quote = True
                i += 1
                continue
            if c == ";":           # comment to end of physical line
                break
            if c == "(":
                paren_depth += 1
                i += 1
                continue
            if c == ")":
                paren_depth = max(0, paren_depth - 1)
                i += 1
                continue
            if c.isspace():
                if cur:
                    tokens.append(cur)
                    cur = ""
                i += 1
                continue
            cur += c
            i += 1
        if cur:
            tokens.append(cur)
            cur = ""
        if paren_depth == 0:
            res = flush()
            if res:
                yield res
    res = flush()
    if res:
        yield res


def is_ttl(tok):
    if tok.isdigit():
        return True
    # BIND time units: 1h, 30m, 2d, 1w (single trailing unit)
    return len(tok) > 1 and tok[:-1].isdigit() and tok[-1].lower() in "smhdw"


def parse_bind(text, origin):
    """Parse BIND text into (records, review_lines).

    records: dict[(fqdn, TYPE)] -> set of canonical value strings
    review_lines: list of raw lines we could not parse as standard records
    """
    origin = fqdn(origin)
    default_ttl = None
    last_owner = origin
    records = defaultdict(set)
    review = []

    for raw, toks in logical_lines(text):
        if not toks:
            continue
        # directives
        if toks[0].upper() == "$ORIGIN":
            origin = fqdn(toks[1])
            continue
        if toks[0].upper() == "$TTL":
            default_ttl = toks[1]
            continue
        if toks[0].upper() == "$INCLUDE":
            review.append(raw)            # we don't follow includes
            continue

        idx = 0
        # owner name: present unless the physical line started with whitespace,
        # which the tokenizer has erased -- so we infer: if the first token is a
        # TTL/class/type, the owner was omitted and inherits from the previous line.
        first = toks[0]
        if is_ttl(first) or first.upper() in CLASSES or first.upper() in KNOWN_TYPES:
            owner = last_owner
        else:
            owner = resolve_name(first, origin)
            last_owner = owner
            idx = 1

        ttl = default_ttl
        rclass = "IN"
        rtype = None
        while idx < len(toks):
            t = toks[idx]
            if rtype is None and is_ttl(t):
                ttl = t
                idx += 1
                continue
            if rtype is None and t.upper() in CLASSES:
                rclass = t.upper()
                idx += 1
                continue
            rtype = t.upper()
            idx += 1
            break

        if rtype is None:
            review.append(raw)
            continue

        rdata_toks = toks[idx:]
        if rtype not in KNOWN_TYPES:
            review.append(raw)            # e.g. a cli53 ALIAS pseudo-record
            continue

        rdata_text = " ".join(rdata_toks)
        canon = canonicalize(rtype, rdata_toks, rdata_text, origin)
        if canon is None:
            review.append(raw)
            continue
        records[(owner, rtype)].add(canon)

    return records, review


# --------------------------------------------------------------------------- #
# Name + value canonicalization (so equivalent records compare equal)
# --------------------------------------------------------------------------- #
def fqdn(name):
    name = name.strip()
    if not name.endswith("."):
        name += "."
    return name.lower()


def resolve_name(name, origin):
    if name == "@":
        return origin
    if name.endswith("."):
        return name.lower()
    return (name + "." + origin).lower()


def canonicalize(rtype, toks, rdata_text, origin):
    """Return a canonical string for a record value, or None if unparseable.

    Delegates the hard cases to dnspython; applies type-specific normalization
    so that trailing dots, IPv6 compression, MX preference, and TXT chunking
    don't produce false diffs.
    """
    try:
        if rtype in ("A",):
            return str(ipaddress.ip_address(rdata_text.strip()))
        if rtype in ("AAAA",):
            return str(ipaddress.ip_address(rdata_text.strip()))  # compresses
        if rtype in ("CNAME", "NS", "PTR", "DNAME"):
            return fqdn_target(rdata_text.strip(), origin)
        if rtype == "MX":
            pref, exch = toks[0], toks[1]
            return f"{int(pref)} {fqdn_target(exch, origin)}"
        if rtype == "SRV":
            prio, weight, port, target = toks[0], toks[1], toks[2], toks[3]
            return f"{int(prio)} {int(weight)} {int(port)} {fqdn_target(target, origin)}"
        if rtype in ("TXT", "SPF"):
            return canon_txt(toks)
        if rtype == "CAA":
            flags, tag, value = toks[0], toks[1].lower(), strip_quotes(" ".join(toks[2:]))
            return f"{int(flags)} {tag} {value}"
        # fall back to dnspython's own normalization for anything else
        rd = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.from_text(rtype), rdata_text)
        return rd.to_text().lower()
    except Exception:
        return None


def fqdn_target(name, origin):
    name = name.strip()
    if name in ("@",):
        return origin
    if name.endswith("."):
        return name.lower()
    return (name + "." + origin).lower()


def strip_quotes(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def canon_txt(toks):
    """TXT/SPF: concatenate the (possibly multiple) quoted chunks BIND split a
    long string into, preserving inner content (case-sensitive) and order."""
    parts = []
    for t in toks:
        parts.append(strip_quotes(t))
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Cloudflare API loader
# --------------------------------------------------------------------------- #
def load_cloudflare_api(zone_id, token, origin):
    import requests

    origin = fqdn(origin)
    records = defaultdict(set)
    proxied = set()
    page = 1
    while True:
        r = requests.get(
            f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        for rec in body["result"]:
            rtype = rec["type"].upper()
            name = fqdn(rec["name"])
            canon = canon_cf_record(rec, origin)
            if canon is not None:
                records[(name, rtype)].add(canon)
            if rec.get("proxied"):
                proxied.add((name, rtype))
        info = body["result_info"]
        if page * info["per_page"] >= info["total_count"]:
            break
        page += 1
    return records, proxied


def canon_cf_record(rec, origin):
    rtype = rec["type"].upper()
    content = (rec.get("content") or "").strip()
    try:
        if rtype in ("A", "AAAA"):
            return str(ipaddress.ip_address(content))
        if rtype in ("CNAME", "NS", "PTR", "DNAME"):
            return fqdn_target(content, origin)
        if rtype == "MX":
            return f"{int(rec.get('priority', 0))} {fqdn_target(content, origin)}"
        if rtype == "SRV":
            d = rec.get("data", {})
            return (f"{int(d['priority'])} {int(d['weight'])} {int(d['port'])} "
                    f"{fqdn_target(d['target'], origin)}")
        if rtype in ("TXT", "SPF"):
            return strip_quotes(content)
        if rtype == "CAA":
            d = rec.get("data", {})
            return f"{int(d['flags'])} {str(d['tag']).lower()} {strip_quotes(str(d['value']))}"
        return content.lower()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Diff + report
# --------------------------------------------------------------------------- #
def diff(left, right, ignore_types):
    """left = cli53 (source of truth), right = cloudflare."""
    missing, extra, changed = [], [], []
    keys = set(left) | set(right)
    for key in sorted(keys):
        name, rtype = key
        if rtype in ignore_types:
            continue
        lv = left.get(key, set())
        rv = right.get(key, set())
        if lv == rv:
            continue
        only_left = lv - rv
        only_right = rv - lv
        if lv and not rv:
            missing.append((key, only_left))
        elif rv and not lv:
            extra.append((key, only_right))
        else:
            changed.append((key, only_left, only_right))
    return missing, extra, changed


def report(missing, extra, changed, review, proxied):
    R, G, Y, B, X = "\033[31m", "\033[32m", "\033[33m", "\033[34m", "\033[0m"
    if not sys.stdout.isatty():
        R = G = Y = B = X = ""

    print(f"\n{B}=== DNS migration validation ==={X}")

    print(f"\n{R}MISSING in Cloudflare ({len(missing)})  — present in cli53, absent in CF{X}")
    if not missing:
        print("  (none)")
    for (name, rtype), vals in missing:
        for v in sorted(vals):
            print(f"  {R}- {name:<40} {rtype:<6} {v}{X}")

    print(f"\n{Y}CHANGED value ({len(changed)})  — same name+type, different value set{X}")
    if not changed:
        print("  (none)")
    for (name, rtype), only_l, only_r in changed:
        print(f"  {Y}~ {name:<40} {rtype}{X}")
        for v in sorted(only_l):
            print(f"      cli53 only : {v}")
        for v in sorted(only_r):
            print(f"      cf    only : {v}")

    print(f"\n{B}EXTRA in Cloudflare ({len(extra)})  — added during/after migration{X}")
    if not extra:
        print("  (none)")
    for (name, rtype), vals in extra:
        for v in sorted(vals):
            print(f"  + {name:<40} {rtype:<6} {v}")

    print(f"\n{Y}MANUAL REVIEW ({len(review)})  — non-standard cli53 lines (Route53 ALIAS, etc.){X}")
    if not review:
        print("  (none)")
    for raw in review:
        print(f"  ? {raw}")
    if review:
        print(f"  {Y}These are almost certainly the ELB alias records. Confirm each maps to{X}")
        print(f"  {Y}a Cloudflare CNAME (apex = CNAME-flattened) pointing at the ELB hostname.{X}")

    if proxied:
        print(f"\n{B}Proxied (orange-cloud) records in Cloudflare ({len(proxied)}){X}")
        for name, rtype in sorted(proxied):
            print(f"  ~ {name:<40} {rtype}")

    print(f"\n{B}Summary:{X} {len(missing)} missing, {len(changed)} changed, "
          f"{len(extra)} extra, {len(review)} to review.")
    return 1 if missing else 0


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Validate a Route53->Cloudflare DNS migration.")
    ap.add_argument("--route53", required=True, help="cli53 export --full BIND file")
    ap.add_argument("--origin", required=True, help="zone apex, e.g. example.com")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--cf-bind", help="BIND file exported from Cloudflare dashboard")
    src.add_argument("--cf-api", action="store_true", help="pull records from Cloudflare API")
    ap.add_argument("--zone-id", help="Cloudflare zone id (with --cf-api)")
    ap.add_argument("--include-apex-ns", action="store_true",
                    help="also compare apex NS/SOA (off by default; CF rewrites them)")
    args = ap.parse_args()

    with open(args.route53, encoding="utf-8") as f:
        r53, review = parse_bind(f.read(), args.origin)

    proxied = set()
    if args.cf_bind:
        with open(args.cf_bind, encoding="utf-8") as f:
            cf, _ = parse_bind(f.read(), args.origin)
    else:
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not args.zone_id or not token:
            ap.error("--cf-api needs --zone-id and CLOUDFLARE_API_TOKEN env var")
        cf, proxied = load_cloudflare_api(args.zone_id, token, args.origin)

    ignore = set() if args.include_apex_ns else APEX_REWRITTEN
    missing, extra, changed = diff(r53, cf, ignore)
    sys.exit(report(missing, extra, changed, review, proxied))


if __name__ == "__main__":
    main()
