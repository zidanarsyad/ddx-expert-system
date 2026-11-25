---
title: "DDX-ES: An Explainable Knowledge-Graph Expert System for Differential Diagnosis and Triage"
author: '\IEEEauthorblockN{Muhammad Zidan Arsyad, Muhammad Fikry Rizal and Aditya Wahyu Wijanarko}\IEEEauthorblockA{Department of Computer Science and Electronics, Universitas Gadjah Mada, Yogyakarta, Indonesia}'
abstract: |
  A symptom checker must rank the possible diseases of a patient and decide how urgently care is needed, and a
  clinician must be able to see why. We present DDX-ES, a rule-based expert system whose knowledge, rules and
  reasoning trail are stored in one RDF/OWL 2 knowledge graph. The graph holds the expert knowledge base of the DDXPlus
  benchmark (49 pathologies, 223 questions), <<n_rules>> IF–THEN rules written from <<n_sources>> published guidelines
  and criteria, each with its source and mapping fidelity, and likelihood ratios counted on the validation split. The
  inference engine combines forward chaining with certainty factors, abduction by set covering and a Bayesian
  aggregation that reads certainty factors as likelihood ratios; a safety-first policy sets the triage level, and a
  backward-chaining consultation chooses each question by expected information gain, with a rule-out phase for
  severe diagnoses. Every conclusion is explained (HOW, WHY, WHY NOT) and written back to the graph as W3C PROV-O
  provenance. On the <<n_test>> DDXPlus test patients DDX-ES ranks the true pathology first for <<es_top1>> without
  any training (naive Bayes on the same counts: <<nb_top1>>), triages every emergency as an emergency with
  <<tri_under>> under-triage, and reaches <<c_top1>> top-1 accuracy after <<c_q>> questions in simulated
  consultations.
---

```{=latex}
\begin{IEEEkeywords}
expert system, knowledge graph, OWL 2 RL, certainty factors, set covering, explainability, triage, DDXPlus
\end{IEEEkeywords}
```

# Introduction

Symptom checkers ask a patient a few questions, propose the diseases that could explain the answers and say how
urgently to seek care. Learned models do this accurately, but they cannot say which facts and which clinical guideline
support a decision, and the error that matters most, under-triage, gives an emergency patient a lower urgency without
a reason anyone can check. Classic medical expert systems explain by construction: MYCIN reasoned with certainty
factors over production rules and answered HOW and WHY [@shortliffe1975], and INTERNIST-I chose its questions to rule
out, discriminate or pursue hypotheses [@miller1982]. Their knowledge, however, was hand-coded without sources and
hard to audit, and their explanations showed rule traces rather than the knowledge behind them [@clancey1983].

DDX-ES combines the two traditions. Its **input** is the age, sex and chief complaint of a patient and the answers to
the DDXPlus questionnaire, or a description in plain words that a language model maps onto that questionnaire. Its
**output** is a differential diagnosis with probabilities, a triage level (Emergency, Urgent, Non-urgent, Self-care),
recommended next steps and an explanation of each conclusion. The **domain** is DDXPlus [@ddxplus2022;
@ddxplus2022data]: an expert knowledge base of 49 respiratory, cardiac and emergency pathologies, 223 questions (110
symptoms, 113 antecedents) and <<n_kb_links>> physician-reviewed disease–question links, from which about 1.3 million
synthetic patients were generated. We use the knowledge base as expert knowledge, the validation split
(<<n_val>> patients) only to count how often each answer occurs with each pathology, and the test split (<<n_test>>
patients) only for evaluation.

The contributions are: (i) a knowledge graph that stores the domain, <<n_rules>> rules as data with their sources,
the cases and the provenance of every conclusion, whose OWL 2 RL closure supplies facts the engine relies on; (ii) an
inference engine that combines forward chaining, abduction by set covering and certainty factors read as likelihood
ratios, with a safety-first triage policy and a backward-chaining consultation; (iii) HOW, WHY and WHY NOT
explanations and PROV-O provenance; and (iv) an evaluation on the full DDXPlus test split with an ablation, a triage
analysis, simulated consultations and a validation of every rule.

# Concepts

## Knowledge graph

Knowledge is represented as RDF triples with a hand-written OWL 2 ontology in three parts [@hogan2021]: the domain
(pathologies, evidences, answer values, patients), the rule vocabulary (a rule is a `prov:Plan` with a condition tree,
consequents, a certainty factor and a source) and the provenance model. The graph consists of six Turtle files with
<<kg_total>> asserted triples: the ontology, the DDXPlus knowledge base, the rules, the calibration statistics (one node
per pathology and feature with its count, $P(f\mid d)$, $P(f\mid\neg d)$, likelihood ratio and certainty factor), 980
demo patients, and the OWL 2 RL closure, which adds <<kg_inferred>> triples [@motik2012]. The engine depends on this
closure: the disease profiles exist only as links entailed from sub-property axioms (`:hasSymptom` and
`:hasAntecedent` are sub-properties of `:characterizedBy`), the urgency classes and disease families are entailed from
`owl:hasValue` restrictions, and the red-flag rules are classified as such from their structure, never asserted. The
rules are data: written in YAML, stored as RDF and read back by the engine, and the build checks that this round trip
is lossless. SPARQL queries can therefore audit the rule base and every inference.

## Rules and certainty factors

The <<n_rules>> rules fall into five categories that run as strata (Table \ref{tab:rules}): a rule fires only after
the rules that produce the concepts and scores it reads. Premises combine answers, scales, age, sex, concepts, scores
and beliefs with AND, OR, at-least-$n$-of and NOT, under a three-valued logic in which an unasked question is unknown.
Each rule records its source and how faithfully the DDXPlus questions express the published criterion:
<<fid_faithful>> rules are faithful, <<fid_partial>> partial (an item such as the heart rate cannot be asked) and
<<fid_proxy>> proxies. Certainty factors follow a fixed scale tied to the strength of the source, from 0.8–0.9 for a
case definition or criteria met down to 0.2–0.4 for a supportive feature, and negative values for evidence against.

| Category | Rules | Role |
|:-----------------------|------:|:----------------------------------------|
| Abstraction | <<rc_abstraction>> | answers → clinical concepts |
| Scoring | <<rc_scoring>> | points of clinical scores (Wells, Centor, CRB-65) |
| Diagnostic | <<rc_diagnostic>> | belief in a pathology |
| Triage | <<rc_triage>> | red flags → urgency level |
| Recommendation | <<rc_recommendation>> | next steps (tests, care) |

: The rule base \label{tab:rules}

As in MYCIN, the certainty of a conjunction is the minimum and of a disjunction the maximum of its parts, a rule fires
when its premise reaches 0.2, and its conclusion receives

```{=latex}
\begin{equation}
CF(\text{conclusion}) = CF(\text{rule}) \times CF(\text{premise}) .
\label{eq:prop}
\end{equation}
```

Two contributions to the same concept are combined by MYCIN's parallel combination, $a + b(1-a)$ for $a, b \ge 0$.

## Certainty factors as likelihood ratios

@heckerman1986 showed that if a certainty factor is defined from the likelihood ratio
$\lambda = P(e\mid h)/P(e\mid\neg h)$ as

```{=latex}
\begin{equation}
CF(\lambda)=\begin{cases}(\lambda-1)/\lambda & \lambda\ge 1\\ \lambda-1 & \lambda<1\end{cases}
\label{eq:heck}
\end{equation}
```

then parallel combination equals the multiplication of likelihood ratios, so MYCIN's calculus becomes Bayesian
updating of the odds under conditionally independent evidence:

```{=latex}
\begin{equation}
\log\frac{P(h\mid e_1,\dots,e_n)}{1-P(h\mid e_1,\dots,e_n)} = \log\frac{P(h)}{1-P(h)} + \sum_{i=1}^{n}\log\lambda_i .
\label{eq:odds}
\end{equation}
```

DDX-ES aggregates in this log domain. With a few dozen strong contributions the classic formula pushes several
hypotheses to $CF = 1.0$ within floating-point precision, and the prior alone then decides (Fig. \ref{fig:sat}): stable
and unstable angina, whose profiles are nested, both reached 1.0, and every stable-angina patient of the demo sample
was diagnosed with unstable angina. Summing log likelihood ratios raised top-1 accuracy on the demo patients from 96.9%
to 99.3%.

```{=latex}
\begin{figure*}[t]
\centering
\includegraphics[width=0.78\textwidth]{figures/saturation.png}
\caption{Why DDX-ES aggregates in the log domain. Two hypotheses receive contributions of CF 0.7, one of them two more
than the other. Combined by MYCIN's formula (left) both reach exactly 1.0 in double precision and become a tie that the
prior decides; as log likelihood ratios, Eq.~(\ref{eq:odds}) (right), they stay $2\log_{10}\lambda$ apart.}
\label{fig:sat}
\end{figure*}
```

## Abduction by set covering

Diagnosis is abductive. In the set-covering model [@reggia1983] each disorder $d$ can cause the manifestations
$\mathrm{profile}(d)$, and a parsimonious set of disorders that covers every finding $S$ is sought. Under the
single-disorder assumption the candidates are the pathologies with $S \subseteq \mathrm{profile}(d)$, or, if none
exists, those that leave the fewest findings unexplained.

# Workflow and Algorithms

## Workflow

Fig. \ref{fig:arch} shows the architecture. (1) `prepare_data.py` aligns the French codes of the DDXPlus conditions
file with the English question codes, builds the records of the pathologies and questions, parses the answer tokens of
every patient and counts <<n_pairs>> (feature, pathology) pairs on the validation split. (2) `build_kg.py` converts
the records and the rules into RDF, turns the counts into likelihood ratios and certainty factors, and computes the OWL
2 RL closure. (3) At start-up the engine compiles the graph into its working structures. (4) One inference run
(Algorithm \ref{alg:run}) turns the facts of a case into a differential, a triage level and recommendations. (5) In a
consultation the run is repeated after every answer, and the next question is chosen by backward chaining
(Algorithm \ref{alg:consult}). (6) HOW, WHY and WHY NOT are built from the trace and the working memory. (7) The run is
written back to the graph as PROV-O.

```{=latex}
\begin{figure*}[t]
\centering
\includegraphics[width=0.9\textwidth]{figures/architecture.png}
\caption{Architecture of DDX-ES: the knowledge sources and calibration data become a knowledge graph of six Turtle
files, which the inference engine reads; the interfaces show the conclusions and their explanations, and every run is
written back as provenance.}
\label{fig:arch}
\end{figure*}
```

## Inference

An inference run has four phases (Fig. \ref{fig:pipe}, Algorithm \ref{alg:run}). Forward chaining fires, stratum by
stratum, every rule
whose premise holds, ordered by salience, specificity and position, and each rule at most once (refraction).
Abduction by set covering keeps the candidates. Aggregation adds, for each candidate, the log likelihood ratios of the
rules that fired (their certainty factors converted by Eq.~(\ref{eq:heck})) and of the features of its profile, present
or absent, to its log prior odds, Eq.~(\ref{eq:odds}), and normalises over the candidates. Finally triage and
recommendation rules read the beliefs and scores.

```{=latex}
\begin{figure*}[t]
\centering
\includegraphics[width=0.98\textwidth]{figures/pipeline_paper.png}
\caption{The four phases of one inference run, with what each phase produces for patient T002456.}
\label{fig:pipe}
\end{figure*}
```

```{=latex}
\begin{algorithm}
\caption{One inference run (\texttt{engine.py})}\label{alg:run}
\begin{algo}
\algio{Input:}{facts $F$ of a case; rules in strata $S_0,\dots,S_m$; profiles, likelihood ratios $\lambda$, priors
$\pi$}
\algio{Output:}{probabilities $P$, triage level $t$, advice $A$, trace $T$}
\algnote{Phase 1: forward chaining}
\algline{0}{\textbf{for} $i \gets 0$ \textbf{to} $m$ \textbf{do}}
\algline{1}{$C \gets$ rules of $S_i$ with premise true, $CF \ge 0.2$}
\algline{1}{sort $C$ by salience, specificity, position}
\algline{1}{\textbf{for each} $r \in C$ \textbf{do} \hfill \emph{fires once}}
\algline{2}{$c \gets CF(r)\cdot CF(\mathrm{premise}(r))$; append $r$ to $T$}
\algline{2}{concept $k$: $F[k] \gets \mathrm{combine}(F[k], c)$}
\algline{2}{score $s$: $F[s] \gets F[s] + \mathrm{points}(r)$}
\algline{2}{diagnosis $d$: keep $c$ for $d$}
\algnote{Phase 2: abduction by set covering}
\algline{0}{$D \gets \{d : S \subseteq \mathrm{profile}(d)\}$, else the fewest unexplained}
\algnote{Phase 3: aggregation}
\algline{0}{\textbf{for each} $d \in D$: $\ell_d \gets \log\frac{\pi_d}{1-\pi_d} + \sum \log \lambda$}
\algline{0}{$P(d) \gets e^{\ell_d} / \sum_{d' \in D} e^{\ell_{d'}}$}
\algnote{Phase 4: triage and advice}
\algline{0}{$t \gets$ most urgent level proposed with $CF \ge 0.5$}
\algline{0}{$A \gets$ advice of the recommendation rules that fire}
\algline{0}{\textbf{return} $P, t, A, T$ \hfill \emph{then written as PROV-O}}
\end{algo}
\end{algorithm}
```

**Triage** combines two kinds of knowledge. The <<rc_triage>> red-flag rules come from the guidelines and do not
depend on the diagnosis (anaphylaxis, suspected coronary syndrome or pulmonary embolism, Ebola after travel). The
severity schema lets every hypothesis with $P \ge 0.2$ propose the level of its DDXPlus severity (1 Emergency, 2
Urgent, 3–4 Non-urgent, 5 Self-care). The most urgent level proposed with $CF \ge 0.5$ is selected: over-triage is
accepted to avoid under-triage.

**Worked example.** Patient T002456 reports loss of consciousness, haemoptysis, dyspnoea, hormone use, active cancer,
recent surgery and swollen calves. Abstraction rules derive signs of deep-vein thrombosis, scoring rules give a Wells
score of 6.5 [@wells2000], and the diagnostic rules D-PE-1 and D-PE-3 fire. Set covering keeps one of the 49
pathologies, pulmonary embolism, which explains all twelve symptoms, with $P = 1.00$. The red-flag rule T-PE
[@konstantinides2020] sets Emergency, overriding the Urgent level proposed for the loss of consciousness, and R-PE-HIGH
recommends CT pulmonary angiography.

## Consultation

The consultation starts, like the DDXPlus platform, from age, sex and the chief complaint (open world: every other
question is unknown) and runs Algorithm \ref{alg:run} after every answer. The next question is chosen by backward
chaining from goals (Algorithm \ref{alg:consult}). A rule-out phase keeps the consultation from stopping while a
severe diagnosis is still possible; it was added after the evaluation showed that stopping on confidence alone missed
urgent cases. The consultation can also start from free text in English or Bahasa Indonesia: a language model maps the
description onto the questionnaire, every finding must quote the words it was read from, and the user confirms the
reading before the first question.

```{=latex}
\begin{algorithm}
\caption{The consultation (\texttt{consult.py})}\label{alg:consult}
\begin{algo}
\algio{Input:}{age, sex, chief complaint; findings read from a description, if any}
\algio{Output:}{the questions asked, each with its WHY, and the last run}
\algline{0}{$F \gets$ the facts given; all else unknown}
\algline{0}{\textbf{loop} (at most 30 questions)}
\algline{1}{$(P, t, A, T) \gets$ Algorithm \ref{alg:run} on $F$; $q \gets$ none}
\algline{1}{\textbf{if} $\max_d P(d) < 0.95$ \textbf{then} \hfill \emph{discriminate}}
\algline{2}{$G \gets$ five leading hypotheses + severe ones with $P \ge 0.02$}
\algline{2}{$Q \gets$ unknown conditions of their rules, unasked questions of their profiles}
\algline{2}{$q \gets \arg\max_{x \in Q} \mathrm{IG}(x)\cdot w(x)$; none if $\mathrm{IG}(q) < 0.005$}
\algline{1}{\textbf{if} $q$ is none \textbf{then} \hfill \emph{rule out}}
\algline{2}{$q \gets$ most specific question of a severe hypothesis still possible ($P \ge 10^{-4}$), at most two each}
\algline{1}{\textbf{if} $q$ is none \textbf{then stop}}
\algline{1}{ask $q$, show its WHY, add the answer to $F$}
\end{algo}
\end{algorithm}
```

## Explanation and provenance

**HOW** draws a proof tree from a conclusion through the rules that fired, each with its certainty factor and source,
down to the patient's answers (Fig. \ref{fig:how}). **WHY** names, for the question being asked, the goal hypothesis
with its probability and severity, the rule that needs the answer, its source and the expected information gain.
**WHY NOT** explains why another disease was not concluded: which reported symptom its profile cannot explain, and
which conditions of each of its rules failed or are still unknown. After every run, each rule firing is written to the
graph as a `prov:Activity` that used certain facts and followed a rule, and each conclusion as a `prov:Entity`
generated by a firing, so that SPARQL [@harris2013] can answer audit questions such as "why is this case an
emergency?". The web interface can also write a run into a GraphDB repository as a named graph of its own, through the
RDF4J REST interface [@rdf4jrest], where the same queries return the same rows.

```{=latex}
\begin{figure*}[t]
\centering
\includegraphics[width=0.86\textwidth]{figures/ui-proof-graph.png}
\caption{HOW explanation of patient T002456 as drawn by the web interface: the patient's answers (yellow) feed
abstraction and scoring rules, each with the certainty factor it adds and its source; the derived facts (grey) feed the
diagnostic rules that conclude pulmonary embolism.}
\label{fig:how}
\end{figure*}
```

# Results

## Setup

The main experiment runs on all <<n_test>> test patients with complete answers. The metrics follow the DDXPlus paper:
GTPA@$k$ (the true pathology among the $k$ most probable) and DDF1 (F1 of the predicted against the reference
differential). For triage the reference is the level implied by the severity of the true pathology. As a machine-
learning baseline, a naive-Bayes classifier [@domingos1997] is built from the same counts; the hybrid pools its
log-odds with those of the expert system.

## Diagnosis and triage

DDX-ES ranks the true pathology first for <<es_top1>> of the test patients, ahead of naive Bayes (Table
\ref{tab:acc}, Fig. \ref{fig:res}, left); the true pathology is missing from its top three for <<es_top3_miss>> of the <<n_test>> patients.
DDF1 is low by design, because
DDX-ES commits to a diagnosis while the reference differential lists about ten. Every emergency is triaged as an
emergency (<<tri_emergency_recall>>), under-triage is <<tri_under>> and over-triage <<tri_over>>: most over-triage comes
from guideline red flags that disagree with the DDXPlus severity scale, for example a suspected pulmonary embolism,
which the ESC guideline treats as an emergency and DDXPlus as urgent. A run takes <<ms>> ms per patient.

| Metric | DDX-ES | Naive Bayes | Hybrid |
|:----------------|-------:|-------:|-------:|
| Top-1 (GTPA@1) | **<<es_top1>>** | <<nb_top1>> | <<hy_top1>> |
| Top-3 (GTPA@3) | <<es_top3>> | <<nb_top3>> | <<hy_top3>> |
| Top-5 (GTPA@5) | <<es_top5>> | <<nb_top5>> | <<hy_top5>> |
| DDF1 | <<es_ddf1>> | <<nb_ddf1>> | <<hy_ddf1>> |

: Diagnostic accuracy on the <<n_test>> test patients \label{tab:acc}

## Ablation

An ablation on <<abl_n>> stratified test patients (Fig. \ref{fig:res}, middle) shows where the ranking accuracy comes
from. The guideline rules alone describe syndromes that several pathologies share and rank the true pathology first
for only <<abl_rules>>; set covering alone reaches <<abl_cover>>, the two together <<abl_rules_cover>>. The calibrated
knowledge-base links account for most of the accuracy: <<abl_kb_cover>> with covering, as much as the full system
(<<abl_full>>). The guideline rules contribute elsewhere: they derive the concepts and scores, drive triage and
recommendations, and are the rules the explanations cite.

```{=latex}
\begin{figure*}[t]
\centering
\includegraphics[width=0.98\textwidth]{figures/paper_results.png}
\caption{Results at a glance. Left: top-1 error on the full test split (lower is better). Middle: top-1 accuracy of
engine configurations on a stratified sample. Right: urgent cases that ended with a non-urgent triage in simulated
consultations, without and with the rule-out phase.}
\label{fig:res}
\end{figure*}
```

## Consultation

The consultation was simulated on the 980 demo patients by answering every question from the patient's record (Table
\ref{tab:cons}). Without training on any patient, DDX-ES asks fewer questions than the reinforcement-learning agents of
the DDXPlus paper, which were trained on about a million patients; the comparison is indicative, since the paper
reports the whole test split. The rule-out phase cut the urgent cases that ended with a non-urgent triage from
<<c0_missed>> to <<c_missed>> for <<c_extra_q>> extra questions (Fig. \ref{fig:res}, right).

| System | Questions | Top-1 | Trained |
|:------------------------------|------:|------:|:------|
| AARLC [@ddxplus2022] | 6.73 | 99.21% | yes |
| BASD [@ddxplus2022] | 17.99 | 97.15% | yes |
| DDX-ES, stop at $P \ge 0.95$ | <<c_q>> | <<c_top1>> | no |
| DDX-ES, stop at $P \ge 0.99$ | <<c99_q>> | <<c99_top1>> | no |

: Interactive consultation \label{tab:cons}

## Rules and free-text intake

Every rule was run on a stratified sample of <<rv_n>> validation patients. All <<n_rules>> rules fire at least once,
and the
positive diagnostic rules have a median precision of <<rv_median_prec>> and a median lift of <<rv_median_lift>>; the
weakest are broad screening criteria written for sensitivity, such as the WHO influenza-like-illness definition
[@who2013ili]. The optional free-text intake was tested on <<in_n>> demo patients with descriptions that a language
model wrote in English and in Indonesian. It read the findings with <<in_en_prec>> and <<in_id_prec>> precision
against the records, after which the consultation asked <<in_en_q>> questions on average, with a top-1 accuracy of
<<in_en_top1>> and <<in_id_top1>>, against <<in_base_top1>> after <<in_base_q>> questions from the chief complaint
alone.

# Discussion

The patients are synthetic and were generated from the same knowledge base that DDX-ES uses, so set covering is exact
here; on real patients, with comorbidities and noisy answers, accuracy would be lower and a soft covering would be
needed. Summing log likelihood ratios assumes conditionally independent evidence, and the guideline rules partly
re-count answers that the links already count, so the posteriors are overconfident although the ranking does not
suffer. <<fid_partial_proxy>> rules only approximate their criteria because the questionnaire cannot ask for vital
signs, tests or the duration of symptoms, and the certainty factors of the rules, assigned on a fixed scale, agree
only moderately with the precision measured on held-out patients. DDX-ES is an educational prototype on synthetic data,
not a medical device; its rules paraphrase published criteria, which remain the authority.

# Conclusion

DDX-ES shows that a classic rule-based expert system on a knowledge graph can be accurate, safe and transparent at the
same time. One RDF/OWL 2 graph holds the DDXPlus knowledge and <<n_rules>> cited rules as data, and its OWL 2 RL
closure supplies facts the engine needs. Forward chaining, set covering and certainty factors read as likelihood
ratios rank the true pathology first for <<es_top1>> of <<n_test>> test patients without training, a safety-first
policy triages every emergency as an emergency, and a backward-chaining consultation with a rule-out phase reaches
<<c_top1>> after <<c_q>> questions. Every conclusion can be traced to its rule, the facts it used and its source.
Future work: validating the rules with clinicians on real, de-identified consultations; calibrating the rule
certainty factors from data; an early red-flag screen in the consultation; SHACL validation of the rule base; and a
questionnaire in Bahasa Indonesia.

# References {.unnumbered}

```{=latex}
\footnotesize
```

::: {#refs}
:::
