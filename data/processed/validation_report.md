# Data preparation report

Source: DDXPlus (Fansi Tchango et al., 2022), figshare doi:10.6084/m9.figshare.20043374, CC BY 4.0

| # | Check | Result | Detail |
|---|---|---|---|
| 1 | French evidence codes aligned to English E_xx codes | PASS | 223/223 matched on (question_fr, is_antecedent, data_type); 0 ambiguous |
| 2 | 49 pathologies, each with an ICD-10 chapter and a severity | PASS | 49 pathologies in 12 ICD-10 chapters; severity levels [1, 2, 3, 4, 5] |
| 3 | Every knowledge-base symptom/antecedent resolves to an evidence | PASS | 888 pathology–evidence links |
| 4 | Parent questions (code_question) exist in the evidence file | NOTE | 5 parent codes are missing from the release (E_117, E_122, E_36, E_68, E_85); sub-question links are kept only where the parent exists |
| 5 | Knowledge-base kind (symptom/antecedent) matches the evidence flag | NOTE | 1 links disagree: E_16 listed as symptom |
| 6 | Pathology names in patient files match the knowledge base | PASS | test 134,529 patients, validation 132,448 patients, 49/49 names resolve |
| 7 | Stratified test sample | PASS | 980 patients = 20 per pathology (seed 42) |
| 8 | Every symptom a patient reports is in the knowledge-base profile of his pathology | PASS | 1,315,219 of 1,315,219 symptom observations are covered (basis of the set-covering step) |
| 9 | Every antecedent a patient reports is in the knowledge-base profile of his pathology | PASS | 462,735 of 462,735 antecedent observations are covered |
