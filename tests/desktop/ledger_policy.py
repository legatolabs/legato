"""Whole-request policies. Each sees exactly the outgoing request text and the declared ledger command schema."""
import json
import os
import re
import urllib.request

OP={"type":"object","properties":{"op":{"type":"string","enum":["set","copy","add","decline"]},
    "account":{"type":"integer","minimum":1,"maximum":12},
    "arg":{"type":"integer","minimum":0,"maximum":999}},
    "required":["op","account","arg"],"additionalProperties":False}
BATCH={"type":"object","properties":{"steps":{"type":"array","items":OP}},"required":["steps"],"additionalProperties":False}
SYSTEM=("You translate customer ledger adjustments into a tool's typed instructions. Preserve the order. "
        "account is the destination account number, 1 through 12. For set, arg is the new value. "
        "For add, arg is the increment. For copy, arg is the source account number. "
        "Examples: 'set account 5 to 705' => set,5,705. 'copy account 3 to account 8' => copy,8,3. "
        "'Add 6 to account 7' => add,7,6. The tool will compute the resulting balance. Never invent an account. For unsupported, ambiguous, conditional or negated requests choose decline, account 1, arg 0.")
RULES=[
    (re.compile(r"Set account (\d+) to (\d+)\."),"set",False),
    (re.compile(r"Copy account (\d+) to account (\d+)\."),"copy",True),
    (re.compile(r"Increase account (\d+) by (\d+)\."),"add",False)
]


def strings(step,wording):
    op,row,n=step; row+=1
    if op=="set":return [f"Set account {row} to {n}.",f"The new balance for account {row} is {n}.",
                         f"Assign a balance of {n} to account {row}."][wording]
    if op=="copy":return [f"Copy account {n+1} to account {row}.",
                          f"Give account {row} the current balance of account {n+1}.",
                          f"Use the value in account {n+1} as the new balance of account {row}."][wording]
    return [f"Increase account {row} by {n}.",f"Credit another {n} to account {row}.",
            f"Raise the balance of account {row} by {n}."][wording]


def registered(line):
    for pattern,kind,reverse in RULES:
        m=pattern.fullmatch(line)
        if m:
            a,b=map(int,m.groups())
            return {"op":kind,"account":b if reverse else a,"arg":a if reverse else b}
    return None


def ask(lines,multi):
    schema=BATCH if multi else OP
    system=SYSTEM+(" Return one object with a steps array, one entry per numbered line." if multi else " Return a single command object.")
    prompt="\n".join(f"{i+1}. {s}" for i,s in enumerate(lines)) if multi else lines[0]
    body={"model":"legato-overall","messages":[{"role":"system","content":system},{"role":"user","content":prompt}],
          "temperature":0,"seed":3297,"max_tokens":520 if multi else 75,"cache_prompt":False,
          "response_format":{"type":"json_schema","json_schema":{"name":"plan","strict":True,"schema":schema}}}
    req=urllib.request.Request("http://127.0.0.1:18714/v1/chat/completions",data=json.dumps(body).encode(),
                                headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=150) as r:
        c=json.load(r)["choices"][0]
    if c["finish_reason"]!="stop":raise ValueError("incomplete generation")
    result=json.loads(c["message"]["content"])
    values=result["steps"] if multi else [result]
    if len(values)!=len(lines):raise ValueError("wrong number of commands")
    for v in values:
        if v["account"] not in range(1,13) or (v["op"]=="copy" and v["arg"] not in range(1,13)):
            raise ValueError("outside document scope")
    return values


def plan(lines,mode):
    if mode=="batch":return ask(lines,True),1
    parsed=[registered(line) for line in lines]
    indices=[i for i,v in enumerate(parsed) if v is None]
    if indices:
        for i,guess in zip(indices,ask([lines[i] for i in indices],True)):
            parsed[i]=guess
    return parsed,int(bool(indices))

# The two integer arguments must be copied from the caller's instruction, in their
# original order. The model supplies only the operation and direction.
CODES = {"A":("set",False), "B":("set",True), "C":("copy",False), "D":("copy",True),
         "E":("add",False), "F":("add",True)}
CODE_PROMPT = """Choose one command code per numbered ledger instruction, returning the letters joined in order.
The first and second numbers in the original instruction are replaced with {a} and {b}. Never invent a number.
A: set account {a} to value {b}. B: set account {b} to value {a}.
C: set account {a} to the current balance of account {b}. D: set account {b} to the current balance of account {a}.
E: increase account {a} by amount {b}. F: increase account {b} by amount {a}.
G: unsupported, ambiguous, conditional, negated, or asks for no write. Each line has exactly one code.
For example: copy account {a} to account {b} means D; credit {a} to account {b} means F."""


class Declined(ValueError):
    pass


def spans(lines, canonical_shortcut):
    found=[registered(s) if canonical_shortcut else None for s in lines]
    variables={}
    text=[]
    for i,line in enumerate(lines):
        if found[i] is not None:continue
        matches=list(re.finditer(r"\b\d+\b",line))
        if len(matches)!=2:raise Declined("expected two explicit numeric arguments")
        a,b=matches
        variables[i]=int(a.group()),int(b.group())
        text.append(line[:a.start()]+"{a}"+line[a.end():b.start()]+"{b}"+line[b.end():])
    if not text:return found,0
    schema={"type":"string","pattern":"^[A-G]{"+str(len(text))+"}$"}
    body={"model":"legato-overall","messages":[{"role":"system","content":CODE_PROMPT},
          {"role":"user","content":"\n".join(f"{i+1}. {s}" for i,s in enumerate(text))}],
          "temperature":0,"seed":3297,"max_tokens":24,"cache_prompt":False,
          "response_format":{"type":"json_schema","json_schema":{"name":"bound","strict":True,"schema":schema}}}
    req=urllib.request.Request("http://127.0.0.1:18714/v1/chat/completions",data=json.dumps(body).encode(),
                               headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=120) as r:choice=json.load(r)["choices"][0]
    if choice["finish_reason"]!="stop":raise ValueError("incomplete generation")
    codes=json.loads(choice["message"]["content"])
    if not isinstance(codes,str) or len(codes)!=len(text):raise ValueError("bad arity")
    for i,code in zip(variables,codes):
        if code=="G":raise Declined("declared outside operation catalog")
        if code not in CODES:raise ValueError("invalid operation id")
        op,reverse=CODES[code]
        dest,arg=reversed(variables[i]) if reverse else variables[i]
        found[i]=dict(op=op,account=dest,arg=arg)
    return found,1


def richer(step, j):
    op,row,n=step; account=row+1
    if j<3:return strings(step,j)
    if op=="set":return (f"Replace the balance in account {account} with {n}.",
                          f"Post {n} as the new balance of account {account}.",
                          f"Account {account} should end up with a balance of {n}.")[j-3]
    if op=="copy":return (f"Match account {account} to whatever account {n+1} currently holds.",
                           f"Take the balance of account {n+1} and assign it to account {account}.",
                           f"Bring account {account} into line with account {n+1} by copying its balance.")[j-3]
    return (f"Top up account {account} by {n}.",f"Credit {n} additional units to account {account}.",
            f"Raise account {account}'s present balance by {n}.")[j-3]


def preflight(lines,method):
    if method.startswith("span"):
        plan,calls=spans(lines,method=="span-hybrid")
    elif method=="serial":
        plan=[ask([line],False)[0] for line in lines];calls=len(lines)
    else:plan=ask(lines,True);calls=1
    for v in plan:
        if v["op"]=="decline":raise Declined("declared outside operation catalog")
        if v["account"] not in range(1,13) or (v["op"]=="copy" and v["arg"] not in range(1,13)):
            raise ValueError("account outside document")
        if v["op"]=="set" and v["arg"] not in range(100,1000):raise ValueError("balance outside document")
    return plan,calls
