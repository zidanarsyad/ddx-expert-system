"""Explanation facility: HOW a conclusion was reached, WHY NOT another one, and the rule trace.

Following Clancey (1983), an explanation shows the rules that fired, the facts each rule matched, the certainty
arithmetic, and -- for every rule -- the published source it encodes. Proof trees are plain dicts so the CLI
(Markdown), the web UI (expanders and Graphviz) and the RDF provenance writer can all render them.
"""
from __future__ import annotations

import textwrap

from . import rulebase as rb
from .certainty import combine_all
from .engine import Eval, Fact, Result, _WorkingMemory


class Explainer:
    def __init__(self, engine):
        self.engine, self.kb = engine, engine.kb
        self.r = self.kb.renderer

    # ----------------------------------------------------------------------------- facts and rules as text
    def fact_text(self, f: Fact) -> str:
        kb = self.kb
        if f.kind == "evidence":
            return ("yes: " if f.value else "no: ") + kb.label("evidence", f.key)
        if f.kind == "answer":
            return f"{kb.label('evidence', f.key)}: " + ", ".join(kb.label("value", v) for v in f.value)
        if f.kind == "scale":
            return f"{kb.label('evidence', f.key)}: {f.value}/10"
        if f.kind == "age":
            return f"age {f.value}"
        if f.kind == "sex":
            return "female" if f.value == "F" else "male"
        if f.kind == "concept":
            return (kb.label("concept", f.key) + (f" (CF {f.cf:.2f})" if f.value else " — does not hold"))
        if f.kind == "score":
            return f"{kb.label('score', f.key)} = {f.value:g}"
        if f.kind == "belief":
            return f"P({kb.label('pathology', f.key)}) = {f.value:.2f}"
        if f.kind == "not":
            return "not: " + self.r.cond(f.key)
        return str(f)

    def rule_info(self, r: rb.Rule) -> dict:
        return {"id": r.id, "name": r.name, "category": r.category, "text": self.kb.rule_text(r), "cf": r.cf,
                "source": r.source, "citation": self.kb.citation(r.source), "short": self.kb.short_citation(r.source),
                "link": self.kb.source_link(r.source), "fidelity": r.fidelity, "note": r.note,
                "classes": sorted(self.kb.rule_classes.get(r.id, ()))}

    def firing_text(self, firing) -> str:
        outs = ", ".join(f"{self._consequent_label(c)} {v:+.2f}" if c.kind != "score" else f"{self._consequent_label(c)} +{v:g}"
                         for c, v in firing.outputs)
        return f"[{firing.rule.id}] {firing.rule.name} -> {outs}  ({self.kb.short_citation(firing.rule.source)})"

    def _consequent_label(self, c) -> str:
        kind = {"concept": "concept", "diagnosis": "pathology", "score": "score", "recommend": "recommendation"}.get(c.kind)
        return rb.TRIAGE_LABEL[c.target] if c.kind == "triage" else self.kb.label(kind, c.target)

    # ----------------------------------------------------------------------------- proof trees (HOW)
    def _fact_node(self, result: Result, f: Fact, depth: int) -> dict:
        node = {"kind": f.kind, "label": self.fact_text(f), "children": []}
        if f.kind == "concept" and f.value and depth < 8:
            node["children"] = [self._firing_node(result, x, depth + 1) for x in result.concept_firings.get(f.key, [])]
        elif f.kind == "score" and depth < 8:
            node["children"] = [self._firing_node(result, x, depth + 1) for x in result.score_firings.get(f.key, [])]
        elif f.kind in ("evidence", "answer", "scale", "age", "sex"):
            node["origin"] = "patient"
        return node

    def _firing_node(self, result: Result, firing, depth: int = 0) -> dict:
        info = self.rule_info(firing.rule)
        cfs = [v for c, v in firing.outputs if c.kind != "score"]
        return {"kind": "rule", "label": f"{info['id']}: {info['name']}", "rule": info, "step": firing.step,
                "premise_cf": firing.premise_cf, "cf": cfs[0] if cfs else None,
                "children": [self._fact_node(result, f, depth + 1) for f in firing.used]}

    def how(self, result: Result, kind: str, key: str) -> dict:
        """Proof tree for a concept, a score, a pathology, the triage level or a recommendation."""
        if kind == "concept":
            fs = result.concept_firings.get(key, [])
            return {"kind": "concept", "label": self.kb.label("concept", key), "cf": result.concepts.get(key),
                    "children": [self._firing_node(result, f) for f in fs]}
        if kind == "score":
            return {"kind": "score", "label": f"{self.kb.label('score', key)} = {result.scores.get(key, 0):g}",
                    "children": [self._firing_node(result, f) for f in result.score_firings.get(key, [])]}
        if kind == "recommendation":
            fs = [f for rec, cf, f in result.recommendations if rec == key]
            return {"kind": "recommendation", "label": self.kb.label("recommendation", key),
                    "children": [self._firing_node(result, f) for f in fs]}
        if kind == "triage":
            return self.triage(result)
        if kind == "pathology":
            return self.diagnosis(result, key)
        raise ValueError(kind)

    def diagnosis(self, result: Result, d: str, top_features: int = 6) -> dict:
        kb, h = self.kb, result.hypothesis(d)
        p = kb.pathologies[d]
        present = sorted(result.case.present_symptoms(kb))
        if h.excluded:
            covering = {"excluded": True, "explains": len(present) - len(h.unexplained), "of": len(present),
                        "unexplained": [kb.label("evidence", e) for e in h.unexplained]}
        else:
            covering = {"excluded": False, "explains": len(present) - len(h.unexplained), "of": len(present),
                        "unexplained": [kb.label("evidence", e) for e in h.unexplained]}
        rules = [c for c in h.contributions if c.kind == "rule"]
        kb_pos = sorted((c for c in h.contributions if c.kind in ("kb", "kb-absent") and c.cf > 0), key=lambda c: -c.cf)
        kb_neg = sorted((c for c in h.contributions if c.kind in ("kb", "kb-absent") and c.cf < 0), key=lambda c: c.cf)
        feat = lambda c: {"feature": kb.feature_label(c.strength.feature) if c.kind == "kb" else "absent: " + kb.label("evidence", c.strength.feature),  # noqa: E731
                          "p_d": c.strength.p_d, "p_other": c.strength.p_other, "lr": c.strength.lr, "cf": c.cf}
        rule_nodes = [self._firing_node(result, c.firing) for c in rules]
        excluded_rules = [] if not h.excluded else [self._firing_node(result, c.firing) for c in result.rule_contributions.get(d, [])]
        cf_rules = combine_all(c.cf for c in rules)
        cf_kb = combine_all(c.cf for c in h.contributions if c.kind in ("kb", "kb-absent"))
        return {"kind": "pathology", "pathology": d, "label": p.name, "rank": h.rank, "probability": h.probability,
                "cf": h.cf, "prior": h.prior, "severity": p.severity, "icd10": p.icd10, "classes": sorted(p.classes),
                "covering": covering, "cf_rules": cf_rules, "cf_kb": cf_kb,
                "n_kb": sum(1 for c in h.contributions if c.kind in ("kb", "kb-absent")),
                "rules": rule_nodes, "rules_fired_but_excluded": excluded_rules,
                "kb_for": [feat(c) for c in kb_pos[:top_features]], "kb_against": [feat(c) for c in kb_neg[:top_features]],
                "children": rule_nodes}

    def triage(self, result: Result) -> dict:
        reasons = []
        for t in result.triage_reasons:
            if t.firing is not None:
                reasons.append(self._firing_node(result, t.firing))
            else:
                p = self.kb.pathologies[t.pathology]
                reasons.append({"kind": "schema", "label": f"KB-SEVERITY: P({p.name}) = {t.cf:.2f} ≥ "
                                f"{result.settings['severity_threshold']:g} and its DDXPlus severity is {p.severity}",
                                "children": []})
        others = [t for t in result.triage_candidates if t not in result.triage_reasons]
        return {"kind": "triage", "label": rb.TRIAGE_LABEL[result.triage], "level": result.triage,
                "rule": "most urgent level proposed with CF ≥ %.1f (safety first)" % result.settings["triage_cf"],
                "children": reasons, "other_candidates": len(others)}

    # ----------------------------------------------------------------------------- WHY NOT
    def _wm_from(self, result: Result) -> _WorkingMemory:
        wm = _WorkingMemory(self.engine, result.case)
        wm.evals, wm.concepts = dict(result.rule_evals), dict(result.concepts)
        wm.scores.update(result.scores)
        wm.prob = {h.pathology: h.probability for h in result.hypotheses if not h.excluded}
        return wm

    def condition_tree(self, result: Result, cond, wm=None) -> dict:
        """Evaluate every node of a premise and report which conditions hold, fail or are unknown."""
        wm = wm or self._wm_from(result)
        ev: Eval = self.engine.evaluate(cond, wm)
        node = {"text": self.r.cond(cond), "status": ev.status,
                "missing": sorted(self.kb.label("evidence", e) if e in self.kb.evidences else e for e in ev.missing)}
        if isinstance(cond, (rb.AllOf, rb.AnyOf, rb.AtLeast)):
            node["children"] = [self.condition_tree(result, c, wm) for c in cond.items]
        elif isinstance(cond, rb.Not):
            node["children"] = [self.condition_tree(result, cond.item, wm)]
        return node

    def why_not(self, result: Result, d: str) -> dict:
        kb, h = self.kb, result.hypothesis(d)
        top = result.hypotheses[0]
        out = {"pathology": d, "label": kb.pathologies[d].name, "rank": h.rank, "probability": h.probability,
               "excluded": h.excluded, "top": kb.pathologies[top.pathology].name, "top_probability": top.probability}
        if h.excluded:
            out["reason"] = ("Set covering: the expert knowledge base has no link from this pathology to "
                             + "; ".join(kb.label("evidence", e) for e in h.unexplained)
                             + ", so it cannot explain these reported symptoms.")
        else:
            negs = sorted((c for c in h.contributions if c.cf < 0), key=lambda c: c.cf)[:5]
            out["reason"] = (f"It explains every reported symptom but ranks {h.rank} with P = {h.probability:.2f}; "
                             f"the evidence against it outweighs the evidence for it compared with {out['top']}.")
            out["against"] = [{"feature": (kb.feature_label(c.strength.feature) if c.kind == "kb"
                                           else "absent: " + kb.label("evidence", c.strength.feature)) if c.strength else c.kind,
                               "cf": c.cf} for c in negs]
        wm = self._wm_from(result)
        near = []
        for r in self.kb.rules:
            if r.category == "diagnostic" and any(c.kind == "diagnosis" and c.target == d and r.consequent_cf(c) > 0 for c in r.consequents):
                fired = result.fired(r.id)
                near.append({"rule": self.rule_info(r), "fired": fired,
                             "tree": None if fired else self.condition_tree(result, r.premise, wm)})
        out["guideline_rules"] = near
        return out

    # ----------------------------------------------------------------------------- whole-case report
    def answers(self, case) -> list[str]:
        """The patient's positive answers as readable text, sub-questions folded into their values."""
        kb, out = self.kb, []
        for e in sorted(case.present(kb), key=lambda x: int(x[2:])):
            if e in case.answers:
                vals = [kb.label("value", v) for v in sorted(case.answers[e]) if v != kb.evidences[e].default]
                out.append(f"{kb.label('evidence', e)}: {', '.join(vals)}")
            elif e in case.scales:
                out.append(f"{kb.label('evidence', e)}: {case.scales[e]}/10")
            else:
                out.append(kb.label("evidence", e))
        return out

    def trace_lines(self, result: Result) -> list[str]:
        lines, last = [], None
        for f in result.trace:
            if f.stratum != last:
                lines.append(f"-- stratum {f.stratum}" + (" (after belief aggregation)" if f.stratum > self.engine.aggregation_stratum else ""))
                last = f.stratum
            lines.append(f"{f.step:3d}. " + self.firing_text(f))
        return lines

    def report(self, result: Result, top_k: int = 5, trace: bool = False) -> str:
        kb, c = self.kb, result.case
        out = [f"# DDX-ES consultation: {c.id}", ""]
        demo = ", ".join(x for x in [f"age {c.age}" if c.age is not None else "", {"F": "female", "M": "male"}.get(c.sex, "")] if x)
        out.append(f"Patient: {demo}. Reported: " + "; ".join(self.answers(c)) + ".")
        out += ["", f"## Triage: {rb.TRIAGE_LABEL[result.triage]}", ""]
        tri = self.triage(result)
        for n in tri["children"]:
            out.append(f"- {n['label']}" + (f" — {n['rule']['short']}" if n.get("rule") else ""))
        out += ["", "## Differential diagnosis", "", "| # | Pathology | P | CF | Covers symptoms | Guideline rules |", "|---|---|---|---|---|---|"]
        for h in result.differential()[:top_k]:
            fired = sorted({x.firing.rule.id for x in h.contributions if x.kind == "rule"})
            present = len(c.present_symptoms(kb))
            out.append(f"| {h.rank} | {kb.pathologies[h.pathology].name} | {h.probability:.3f} | {h.cf:+.3f} | "
                       f"{present - len(h.unexplained)}/{present} | {', '.join(fired) or '-'} |")
        if result.differential():
            top = result.differential()[0]
            ex = self.diagnosis(result, top.pathology)
            out += ["", f"### How: {ex['label']}", "",
                    f"- Set covering: explains {ex['covering']['explains']} of {ex['covering']['of']} reported symptoms.",
                    f"- Guideline rules combined: CF {ex['cf_rules']:+.3f}; knowledge-base links ({ex['n_kb']}): CF {ex['cf_kb']:+.3f}; "
                    f"together CF {ex['cf']:+.3f}; prior {ex['prior']:.4f} -> P = {ex['probability']:.3f}."]
            for n in ex["rules"]:
                out.append(f"- [{n['rule']['id']}] {n['rule']['text']}  ({n['rule']['short']})")
            if ex["kb_for"]:
                out.append("- Strongest knowledge-base evidence for: " + "; ".join(
                    f"{x['feature']} (LR {fmt_lr(x['lr'])}, CF {x['cf']:+.2f})" for x in ex["kb_for"][:4]))
        if result.recommendations:
            out += ["", "## Recommendations", ""]
            for rec, cf, f in result.recommendations:
                out.append(f"- {kb.label('recommendation', rec)} ({kb.short_citation(f.rule.source)}; rule {f.rule.id})")
        if result.concepts:
            out += ["", "## Clinical concepts derived", "", ", ".join(kb.label("concept", k) for k in sorted(result.concepts))]
        if trace:
            out += ["", "## Rule trace", "", "```"] + self.trace_lines(result) + ["```"]
        out += ["", "_Educational prototype built on the synthetic DDXPlus data; not for clinical use._"]
        return "\n".join(out)


def fmt_lr(lr: float) -> str:
    """Likelihood ratios of synthetic data can be huge; show them readably."""
    if lr >= 1000:
        return ">1000"
    if lr < 0.001:
        return "<0.001"
    return f"{lr:.2f}" if lr < 10 else f"{lr:.0f}"


def tree_to_markdown(node: dict, depth: int = 0) -> str:
    pad = "  " * depth
    label = node.get("label", "")
    if node.get("kind") == "rule":
        r = node["rule"]
        label = f"**{r['id']}** {r['name']} — CF {node['cf']:+.2f} ({r['short']})" if node.get("cf") is not None else f"**{r['id']}** {r['name']}"
    lines = [f"{pad}- {label}"]
    for ch in node.get("children", []):
        lines.append(tree_to_markdown(ch, depth + 1))
    return "\n".join(lines)


def _wrap(text: str, width: int = 20, max_lines: int = 4) -> str:
    """Wrap a node label onto short lines so that the graph stays narrow."""
    parts = textwrap.wrap(text, width) or [""]
    if len(parts) > max_lines:
        parts = parts[:max_lines]
        parts[-1] = parts[-1][: width - 3].rstrip() + "..."
    return "\\n".join(parts)


def tree_to_dot(node: dict) -> str:
    """Graphviz DOT of a proof tree (rendered by the web UI)."""
    lines = ['digraph proof {', 'rankdir=LR; nodesep=0.1; ranksep=0.22;',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, margin="0.1,0.04"];']
    counter = [0]
    colors = {"rule": "#dbe7f7", "concept": "#e8f3e8", "pathology": "#fbe3d6", "triage": "#f7d7d7",
              "evidence": "#ffffff", "answer": "#ffffff", "scale": "#ffffff", "schema": "#efe7f7", "score": "#fff4cc"}

    def walk(n):
        counter[0] += 1
        me = f"n{counter[0]}"
        if n.get("kind") == "rule":
            text = n["rule"]["id"] + "\\n" + _wrap(n["rule"]["short"])
        else:
            text = _wrap(n.get("label", ""))
        text = text.replace('"', "'")
        lines.append(f'{me} [label="{text}", fillcolor="{colors.get(n.get("kind"), "#f2f2f2")}"];')
        for ch in n.get("children", []):
            child = walk(ch)
            lines.append(f"{child} -> {me};")
        return me

    walk(node)
    lines.append("}")
    return "\n".join(lines)
