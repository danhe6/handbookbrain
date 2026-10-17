"""'Ask about this': a new hire looking at one thing (a decision, a change, a section, a document, a term,
a system, an answer) asks about it in their own words.

The context is assembled from the graph around that one node (no search, so it is fast and on-topic),
then one model call answers like a senior colleague would: plain words, jargon explained, what it means
for the person's work, and who to ask. Every fact cites a section the context came from.

  node ids (same as the UI graph):  D:<decision>  C:<commit sha>  S:<section vid>  P:<page path>
                                    A:<answer id>  E:<Type>:<name>  T:<term>  V:<conversation>  cite:<cite key>
"""
import re

from .config import COMPANY_NAME, MODEL
from .graph import rows
from .llm import complete
from .parse import cite_key
from .queries import _diff, best_window, sid_for

LIMIT = 14000


def _section_block(sids: list[str], focus: str = "", n: int = 4, size: int = 1400) -> tuple[list[str], dict]:
    out, src = [], {}
    if not sids:
        return out, src
    for s in rows("""MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.sid IN $sids AND s.valid_to IS NULL
                     OPTIONAL MATCH (p)-[:OWNED_BY]->(o)
                     RETURN s.sid AS sid, s.heading AS heading, s.text AS text, p.title AS page, p.path AS path,
                            collect(DISTINCT coalesce(o.name, o.handle)) AS owners LIMIT $n""",
                  {"sids": sids, "n": n}):
        c = cite_key(s["sid"])
        src[c] = s["path"]
        out.append(f"[{c}] {s['page']} › {s['heading']} (owners: {', '.join(s['owners']) or 'none'})\n"
                   f"{best_window(s['text'] or '', focus, size)}")
    return out, src


def context(node: str) -> dict:
    kind, _, ref = node.partition(":")
    parts, src, title, label = [], {}, "", kind

    if kind == "cite":
        sid = sid_for(ref)
        r = rows("MATCH (s:Section {sid:$s}) WHERE s.valid_to IS NULL RETURN s.vid AS v", {"s": sid})
        kind, ref = "S", (r[0]["v"] if r else "")

    if kind == "D":
        d = rows("""MATCH (d:Decision {id:$id})-[:DECIDED_IN]->(cv:Conversation)
                    OPTIONAL MATCH (d)-[:DECIDED_BY]->(by:Person)
                    OPTIONAL MATCH (d)-[:IMPLEMENTED_BY]->(c:Commit)
                    OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
                    OPTIONAL MATCH (d)-[:EVIDENCED_BY]->(g:Segment)
                    RETURN d.statement AS st, d.quote AS quote, d.decided_at AS at, d.status AS status, d.about_sid AS about,
                           cv.title AS where, cv.source AS src, cv.cid AS cid, by.name AS by, c.sha AS sha, c.ts AS cts,
                           c.title AS ctitle, m.iid AS mr, collect(DISTINCT g.idx) AS idx LIMIT 1""",
                 {"id": ref})
        if d:
            d = d[0]
            title, label = d["st"], "Decision"
            parts.append(f"DECISION: {d['st']}\nDecided {(d['at'] or '')[:10]} by {d['by'] or 'unknown'} in "
                         f"'{d['where']}' ({d['src']}). Quote: \"{d['quote'] or ''}\"\nStatus: {d['status']}"
                         + (f"; implemented {(d['cts'] or '')[:10]} in MR !{d['mr']} ({d['ctitle']})" if d["sha"] else ""))
            idx = [i for i in d["idx"] if i is not None] or [0]
            seg = rows("""MATCH (g:Segment {cid:$c}) WHERE g.idx >= $a AND g.idx <= $b
                          RETURN g.speaker AS who, g.text AS t ORDER BY g.idx""",
                       {"c": d["cid"], "a": min(idx) - 6, "b": max(idx) + 6})
            convo = "\n".join(f"{x['who'] or ''}: {x['t'] or ''}" for x in seg)
            if convo:
                parts.append("MEETING / CHAT EXCERPT:\n" + convo[:3500])
            if d["sha"]:
                c = context("C:" + d["sha"])
                parts.append(c["text"])
                src.update(c["sources"])
            elif d["about"]:
                b, s2 = _section_block([sid_for(d["about"])], d["st"])
                parts += b
                src.update(s2)

    elif kind == "C":
        c = rows("""MATCH (c:Commit {sha:$sha}) OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
                    OPTIONAL MATCH (a:Person)-[:AUTHORED]->(c)
                    RETURN c.title AS title, c.ts AS ts, m.iid AS mr, m.description AS descr, a.handle AS author""",
                 {"sha": ref})
        if c:
            c = c[0]
            title, label = c["title"] or "Change", "Change"
            parts.append(f"CHANGE: {c['title']} on {(c['ts'] or '')[:10]}" + (f", MR !{c['mr']}" if c["mr"] else "")
                         + (f" by {c['author']}" if c["author"] else "") + (f"\nMR description: {c['descr'][:800]}" if c.get("descr") else ""))
            ch = rows("""MATCH (:Commit {sha:$sha})-[k:CHANGED]->(s:Section)<-[:HAS_SECTION]-(p:Page)
                         OPTIONAL MATCH (s)-[:SUPERSEDES]->(old:Section)
                         RETURN s.sid AS sid, s.heading AS heading, p.title AS page, p.path AS path, k.kind AS kind,
                                s.text AS new, old.text AS old LIMIT 6""", {"sha": ref})
            for x in ch:
                ck = cite_key(x["sid"])
                src[ck] = x["path"]
                parts.append(f"[{ck}] {x['page']} › {x['heading']} ({x['kind']})\nDIFF:\n"
                             + (_diff(x["old"], x["new"], 1800) if x["old"] else best_window(x["new"] or "", "", 1400)))
            dec = rows("""MATCH (d:Decision)-[:IMPLEMENTED_BY]->(:Commit {sha:$sha}) MATCH (d)-[:DECIDED_IN]->(cv:Conversation)
                          OPTIONAL MATCH (d)-[:DECIDED_BY]->(by:Person)
                          RETURN d.statement AS st, d.quote AS quote, d.decided_at AS at, cv.title AS where, by.name AS by""",
                       {"sha": ref})
            for d in dec:
                parts.append(f"DECISION BEHIND IT: {d['st']} - decided {(d['at'] or '')[:10]} by {d['by']} in '{d['where']}'. "
                             f"Quote: \"{d['quote'] or ''}\"")
            try:
                from .entities import impact_of_change
                im = impact_of_change(ref)
                ents = ", ".join(f"{e['label']} {e['name']}" + (f" (also in {e['other_docs']} other docs)" if e["other_docs"] else "")
                                 for e in im["entities"][:12])
                if ents:
                    parts.append("SYSTEMS / METRICS / FIELDS THIS TOUCHES: " + ents)
            except Exception:
                pass

    elif kind == "S":
        s = rows("""MATCH (p:Page)-[:HAS_SECTION]->(s:Section {vid:$v}) OPTIONAL MATCH (s)-[:SUPERSEDES]->(old:Section)
                    OPTIONAL MATCH (c:Commit)-[:CHANGED]->(s)
                    RETURN s.sid AS sid, s.heading AS heading, s.text AS text, p.title AS page, p.path AS path,
                           old.text AS old, c.title AS change, c.ts AS ts""", {"v": ref})
        if s:
            s = s[0]
            ck = cite_key(s["sid"])
            src[ck] = s["path"]
            title, label = f"{s['page']} › {s['heading']}", "Section"
            parts.append(f"[{ck}] {s['page']} › {s['heading']}\n{best_window(s['text'] or '', '', 4000)}")
            if s["old"]:
                parts.append(f"LAST CHANGE ({(s['ts'] or '')[:10]}, {s['change']}):\n{_diff(s['old'], s['text'], 1500)}")
            dec = rows("""MATCH (d:Decision)-[:IMPLEMENTED_BY]->(:Commit)-[:CHANGED]->(:Section {sid:$sid})
                          MATCH (d)-[:DECIDED_IN]->(cv:Conversation) OPTIONAL MATCH (d)-[:DECIDED_BY]->(by:Person)
                          RETURN d.statement AS st, d.decided_at AS at, cv.title AS where, by.name AS by""", {"sid": s["sid"]})
            for d in dec:
                parts.append(f"DECISION: {d['st']} ({(d['at'] or '')[:10]}, {d['by']}, '{d['where']}')")

    elif kind == "P":
        p = rows("""MATCH (p:Page {path:$p}) OPTIONAL MATCH (p)-[:OWNED_BY]->(o)
                    RETURN p.title AS title, collect(DISTINCT coalesce(o.name, o.handle)) AS owners""", {"p": ref})
        if p:
            title, label = p[0]["title"], "Document"
            sids = [r["sid"] for r in rows("MATCH (:Page {path:$p})-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL "
                                           "RETURN s.sid AS sid ORDER BY s.ord", {"p": ref})]
            parts.append(f"DOCUMENT: {p[0]['title']} (owners: {', '.join(p[0]['owners']) or 'none'}); sections: "
                         + "; ".join(x.split("#")[-1] for x in sids[:30]))
            b, s2 = _section_block(sids, "", n=6, size=1100)
            parts += b
            src.update(s2)

    elif kind == "A":
        a = rows("""MATCH (a:Answer {id:$id})-[:ANSWERS]->(qn:Question) OPTIONAL MATCH (a)-[:CITES]->(s:Section)
                    RETURN qn.text AS q, a.text AS text, collect(DISTINCT s.sid) AS sids""", {"id": ref})
        if a:
            title, label = a[0]["q"], "Answer"
            parts.append(f"QUESTION: {a[0]['q']}\nANSWER GIVEN: {a[0]['text']}")
            b, s2 = _section_block(a[0]["sids"], a[0]["q"])
            parts += b
            src.update(s2)

    elif kind in ("E", "T"):
        name = ref.split(":", 1)[1] if kind == "E" and ":" in ref else ref
        label = ref.split(":", 1)[0] if kind == "E" and ":" in ref else "Term"
        title = name
        t = rows("MATCH (t:Term {name:$n}) RETURN t.expansion AS x, t.definition AS d", {"n": name})
        if t:
            parts.append(f"GLOSSARY: {name}" + (f" ({t[0]['x']})" if t[0]["x"] else "") + f": {t[0]['d']}")
        from .glossary import _sections_for
        secs = _sections_for(name, k=4)
        parts.append(f"WHERE '{name}' APPEARS:")
        for s in secs:
            ck = cite_key(s["sid"])
            src[ck] = s["path"]
            i = max((s["text"] or "").find(name), 0)
            parts.append(f"[{ck}] {s['page']} › {s['heading']}\n{(s['text'] or '')[max(0, i - 400):i + 1100]}")

    elif kind == "V":
        v = rows("""MATCH (cv:Conversation {cid:$c}) OPTIONAL MATCH (d:Decision)-[:DECIDED_IN]->(cv)
                    RETURN cv.title AS title, cv.source AS src, cv.started_at AS at, collect(d.id) AS ds""", {"c": ref})
        if v:
            title, label = v[0]["title"], "Conversation"
            parts.append(f"CONVERSATION: {v[0]['title']} ({v[0]['src']}, {(v[0]['at'] or '')[:10]})")
            for did in v[0]["ds"][:3]:
                c = context("D:" + did)
                parts.append(c["text"])
                src.update(c["sources"])

    elif kind in ("O", "U"):
        who = ref
        r = rows("""MATCH (p:Page)-[:OWNED_BY]->(o) WHERE o.handle = $w OR o.name = $w RETURN p.title AS t LIMIT 15""", {"w": who})
        d = rows("""MATCH (d:Decision)-[:DECIDED_BY]->(:Person {name:$w}) RETURN d.statement AS st, d.decided_at AS at LIMIT 8""",
                 {"w": who})
        title, label = who, "Person"
        parts.append(f"PERSON/TEAM: {who}\nOwns: {', '.join(x['t'] for x in r) or 'nothing on record'}\n"
                     f"Decisions: " + ("; ".join(f"{(x['at'] or '')[:10]} {x['st']}" for x in d) or "none on record"))

    text = "\n\n".join(x for x in parts if x)
    return {"node": node, "label": label, "title": title, "text": text[:LIMIT], "sources": src}


SYSTEM = """You are a senior colleague at {company}. A new hire is looking at one item in the company knowledge
graph (below) and asks about it. Answer like a patient, experienced teammate:
- Lead with the direct answer in 1-2 sentences, then only what helps them understand or act.
- Explain jargon and acronyms in a few plain words the first time you use them.
- When it fits the question: why it matters for their day-to-day work, what changed versus before, who to ask.
- Use ONLY the CONTEXT. If it does not contain the answer, say what is missing and who would know (owners named in it).
- Cite facts with the [cite-key] shown in the CONTEXT, e.g. [marketing/marketing-operations/marketo#lead-scoring].
- Under 180 words unless they ask for more. Markdown bullets are fine. No preamble."""


def explain(node: str, question: str, history: list[dict] | None = None) -> dict:
    ctx = context(node)
    if not ctx["text"]:
        return {"answer": "I could not find this item in the graph.", "sources": {}, **{k: ctx[k] for k in ("label", "title")}}
    msgs = [{"role": "system", "content": SYSTEM.format(company=COMPANY_NAME)},
            {"role": "user", "content": f"ITEM: {ctx['label']} - {ctx['title']}\n\nCONTEXT:\n{ctx['text']}"},
            {"role": "assistant", "content": "Got it. What would you like to know about it?"}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:2000]})
    msgs.append({"role": "user", "content": question.strip() or "Explain this to me in plain words."})
    r = complete(MODEL, msgs)
    ans = r.choices[0].message.content or ""
    used = {c: p for c, p in ctx["sources"].items() if c in ans}
    return {"answer": ans, "sources": used, "label": ctx["label"], "title": ctx["title"]}


def suggestions(label: str) -> list[str]:
    return {
        "Decision": ["Explain this decision in plain words", "Why was this decided?", "What does it change in my day-to-day work?",
                     "What was the rule before?"],
        "Change": ["What changed, in plain words?", "Why was this changed?", "What else does this affect?", "Who should I ask about it?"],
        "Section": ["Summarize this for a new hire", "What do the terms here mean?", "When does this apply to me?"],
        "Document": ["What is this document for?", "What should I read first in it?", "Who owns it?"],
        "Answer": ["Explain this answer like I'm new here", "What should I double-check?", "Where can I read more?"],
        "Conversation": ["What was decided here?", "Who was involved?"],
        "Person": ["What does this person own?", "What should I ask them about?"],
    }.get(label, ["What is it, in plain words?", "How is it used here?", "Which documents should I read about it?"])
