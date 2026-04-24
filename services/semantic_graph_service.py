import json
import re
from openai import OpenAI
from config import Config

client = OpenAI(api_key=Config.OPENAI_API_KEY)


SYSTEM_PROMPT = """You are an entity-and-relation extractor that builds a personal knowledge graph from first-person journal writing.

The subject of the graph is always the journal writer ("me"/"user"). Extract concrete, specific entities they mention and the real relationships between them.

Rules:
- "me" is always implicitly present. Only include it as a node if the entity "me" actually connects to something meaningful.
- Do NOT extract generic abstractions ("life", "things", "stuff", "happiness", "it"). Only extract things you could point at.
- People must be real named people or specific roles the writer uses (e.g. "mom", "my manager Raj", "Sara"). Use the exact reference form the writer uses.
- Activities should be specific (e.g. "running at sunrise", not "exercise").
- Emotions only when the writer names the feeling explicitly.
- An edge must describe a real stated relationship, not an inference. Read it back and ask: "did the text actually say this?"
- Keep IDs lowercase with underscores. No punctuation. Use the same ID across repeat mentions.
- If the text is too thin to extract anything substantive, return empty arrays. It is better to return nothing than to hallucinate.

Return ONLY valid JSON matching this schema:
{
  "nodes": [
    {"id": "...", "type": "person|place|activity|emotion|goal|object|concept", "label": "..."}
  ],
  "edges": [
    {"source": "...", "target": "...", "relation": "short verb phrase in user's voice", "weight": 0.0-1.0}
  ]
}

Weight guide: 0.9 = explicitly, strongly stated; 0.6 = stated clearly; 0.3 = mentioned in passing.
"""


def _coerce_id(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:60]


def _clean_graph(raw: dict) -> dict:
    """Normalize IDs, drop junk nodes, drop orphan edges."""
    nodes_in = raw.get("nodes", []) if isinstance(raw, dict) else []
    edges_in = raw.get("edges", []) if isinstance(raw, dict) else []

    banned = {"", "life", "thing", "things", "stuff", "something", "anything",
              "everything", "it", "this", "that", "world", "day", "time"}

    seen_ids = {}
    nodes_out = []
    for n in nodes_in:
        if not isinstance(n, dict): continue
        nid = _coerce_id(n.get("id") or n.get("label"))
        if not nid or nid in banned: continue
        if nid in seen_ids: continue
        label = (n.get("label") or nid.replace("_", " ")).strip()[:60]
        ntype = (n.get("type") or "concept").strip().lower()
        if ntype not in {"person", "place", "activity", "emotion", "goal", "object", "concept"}:
            ntype = "concept"
        node = {"id": nid, "type": ntype, "label": label}
        seen_ids[nid] = node
        nodes_out.append(node)

    edges_out = []
    for e in edges_in:
        if not isinstance(e, dict): continue
        s = _coerce_id(e.get("source"))
        t = _coerce_id(e.get("target"))
        if not s or not t or s == t: continue
        if s not in seen_ids or t not in seen_ids: continue
        rel = (e.get("relation") or "").strip()[:60]
        if not rel: continue
        try:
            w = float(e.get("weight", 0.5))
        except (TypeError, ValueError):
            w = 0.5
        w = max(0.0, min(1.0, w))
        edges_out.append({"source": s, "target": t, "relation": rel, "weight": w})

    return {"nodes": nodes_out, "edges": edges_out}


def extract_entities_and_relations(text: str) -> dict:
    text = (text or "").strip()
    if len(text) < 20:
        return {"nodes": [], "edges": []}

    user_prompt = f"Journal text to extract from:\n\n\"\"\"\n{text}\n\"\"\""

    try:
        response = client.chat.completions.create(
            model=Config.OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=700,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw_text = response.choices[0].message.content or "{}"
    except Exception as e:
        print(f"[graph] extraction call failed: {e}")
        return {"nodes": [], "edges": []}

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback: salvage fenced block.
        m = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not m:
            return {"nodes": [], "edges": []}
        try:
            raw = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"nodes": [], "edges": []}

    return _clean_graph(raw)


def merge_graphs(existing_graph: dict, new_graph: dict) -> dict:
    existing_graph = existing_graph or {}
    existing_nodes = {n["id"]: n for n in existing_graph.get("nodes", [])}
    existing_edges = list(existing_graph.get("edges", []))

    for node in new_graph.get("nodes", []):
        nid = node.get("id")
        if not nid:
            continue
        if nid not in existing_nodes:
            existing_nodes[nid] = node

    def edge_key(e):
        return (e.get("source"), e.get("target"), e.get("relation"))

    existing_by_key = {edge_key(e): e for e in existing_edges}

    for edge in new_graph.get("edges", []):
        k = edge_key(edge)
        if not all(k):
            continue
        if k in existing_by_key:
            prev = existing_by_key[k]
            prev["weight"] = min(1.0, prev.get("weight", 0.5) + 0.15)
        else:
            existing_edges.append(edge)
            existing_by_key[k] = edge

    return {
        "nodes": list(existing_nodes.values()),
        "edges": existing_edges,
    }


def get_graph_summary(nodes: list, edges: list) -> str:
    if not nodes:
        return "No knowledge graph yet. Start journaling to build your semantic map."
    types: dict = {}
    for n in nodes:
        t = n.get("type", "concept")
        types[t] = types.get(t, 0) + 1
    breakdown = ", ".join(f"{k}({v})" for k, v in sorted(types.items(), key=lambda kv: -kv[1]))
    return f"{len(nodes)} concepts · {len(edges)} connections · {breakdown}"
