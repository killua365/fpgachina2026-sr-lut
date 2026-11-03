"""Strict N3 report parser; keeps newer Vivado Advisory severity explicit."""
import re

def drc_summary(text):
    totals=re.findall(r'Checks found:\s*(\d+)',text)
    if len(totals)!=1:raise ValueError('Missing or ambiguous DRC total')
    rules={};counts={s:0 for s in ('Error','Critical Warning','Warning','Info','Advisory')}
    for line in text.splitlines():
        fields=[f.strip() for f in line.split('|')]
        if len(fields)==6 and re.fullmatch(r'[A-Z][A-Z0-9]+-\d+',fields[1]):
            rule,severity,count=fields[1],fields[2],int(fields[4])
            if rule in rules or severity not in counts or count<=0:raise ValueError('Invalid DRC summary row')
            rules[rule]=dict(severity=severity,count=count);counts[severity]+=count
    if sum(counts.values())!=int(totals[0]):raise ValueError('DRC table total mismatch')
    return dict(checks=int(totals[0]),by_severity=counts,rules=rules)
