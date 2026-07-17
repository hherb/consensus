"""Medical case vignettes for evaluation.

Each case has:
- id: unique identifier
- title: short descriptive title
- presentation: the clinical vignette (input to the discussion)
- gold_diagnosis: the confirmed correct diagnosis
- gold_aliases: alternative acceptable names for the diagnosis
- difficulty: easy / moderate / hard
- source: where the case comes from
- key_findings: critical findings that should be identified
- differential: plausible differential diagnoses (for measuring differential quality)
"""

from dataclasses import dataclass, field


@dataclass
class CaseVignette:
    """A medical case for evaluation."""
    id: str
    title: str
    presentation: str
    gold_diagnosis: str
    gold_aliases: list[str] = field(default_factory=list)
    difficulty: str = "moderate"
    source: str = ""
    key_findings: list[str] = field(default_factory=list)
    differential: list[str] = field(default_factory=list)

    def all_acceptable_diagnoses(self) -> list[str]:
        """Return all acceptable forms of the correct diagnosis (lowercased)."""
        return [self.gold_diagnosis.lower()] + [a.lower() for a in self.gold_aliases]


# ---------------------------------------------------------------------------
# Case bank
# ---------------------------------------------------------------------------
# These are classic teaching cases from publicly available medical literature.
# Each is a well-known diagnostic puzzle with a definitive answer.
# ---------------------------------------------------------------------------

CASES: list[CaseVignette] = [
    CaseVignette(
        id="eval-001",
        title="Young woman with joint pain and rash",
        presentation=(
            "A 28-year-old woman presents with a 3-month history of fatigue, "
            "intermittent joint pain affecting the small joints of her hands and "
            "wrists bilaterally, and a facial rash that worsens with sun exposure. "
            "She reports oral ulcers over the past month and recent hair thinning. "
            "On examination, there is a malar rash sparing the nasolabial folds, "
            "synovitis of the MCP and PIP joints, and two shallow oral ulcers. "
            "Laboratory studies show: ANA positive at 1:640, anti-dsDNA positive, "
            "low C3 and C4 complement levels, mild leukopenia (WBC 3.2), and "
            "proteinuria on urinalysis."
        ),
        gold_diagnosis="Systemic Lupus Erythematosus",
        gold_aliases=["SLE", "lupus", "systemic lupus"],
        difficulty="easy",
        source="Classic teaching case",
        key_findings=[
            "Malar rash sparing nasolabial folds",
            "Positive ANA and anti-dsDNA",
            "Low complement levels",
            "Proteinuria suggesting renal involvement",
            "Oral ulcers",
            "Leukopenia",
        ],
        differential=[
            "Rheumatoid arthritis",
            "Mixed connective tissue disease",
            "Drug-induced lupus",
            "Sjögren syndrome",
        ],
    ),
    CaseVignette(
        id="eval-002",
        title="Middle-aged man with progressive weakness",
        presentation=(
            "A 52-year-old man presents with 6 months of progressive proximal "
            "muscle weakness. He has difficulty climbing stairs and rising from "
            "a chair. He also reports a violaceous (heliotrope) discolouration "
            "of his upper eyelids and erythematous, scaly papules over the "
            "dorsal aspects of his MCP and PIP joints (Gottron papules). "
            "Examination reveals symmetric proximal muscle weakness (4/5 in "
            "deltoids and hip flexors). CK is elevated at 4,500 U/L. EMG shows "
            "myopathic changes. Given his age and sex, a CT of chest, abdomen, "
            "and pelvis is performed and reveals a 3 cm pancreatic mass."
        ),
        gold_diagnosis="Dermatomyositis with underlying malignancy",
        gold_aliases=[
            "dermatomyositis",
            "dermatomyositis associated with pancreatic cancer",
            "paraneoplastic dermatomyositis",
        ],
        difficulty="moderate",
        source="Classic teaching case",
        key_findings=[
            "Heliotrope rash",
            "Gottron papules",
            "Proximal muscle weakness",
            "Elevated CK",
            "Myopathic EMG",
            "Underlying malignancy (pancreatic mass)",
        ],
        differential=[
            "Polymyositis",
            "Inclusion body myositis",
            "Statin myopathy",
            "Hypothyroid myopathy",
        ],
    ),
    CaseVignette(
        id="eval-003",
        title="Elderly man with headache and visual loss",
        presentation=(
            "A 74-year-old man presents with a 2-week history of new-onset "
            "severe temporal headache, jaw claudication when chewing, and "
            "sudden painless vision loss in the right eye that occurred this "
            "morning. He reports a 3-month history of fatigue, weight loss of "
            "5 kg, and bilateral shoulder stiffness worse in the mornings. "
            "On examination, the right temporal artery is tender, thickened, "
            "and non-pulsatile. Visual acuity is no light perception in the "
            "right eye with a relative afferent pupillary defect. Fundoscopy "
            "reveals a pale, swollen optic disc on the right. ESR is 95 mm/hr, "
            "CRP is 78 mg/L, and platelets are 480,000."
        ),
        gold_diagnosis="Giant Cell Arteritis with anterior ischaemic optic neuropathy",
        gold_aliases=[
            "giant cell arteritis",
            "GCA",
            "temporal arteritis",
            "cranial arteritis",
            "GCA with AION",
        ],
        difficulty="easy",
        source="Classic teaching case",
        key_findings=[
            "Temporal headache in elderly patient",
            "Jaw claudication",
            "Sudden monocular vision loss",
            "Tender, thickened temporal artery",
            "Markedly elevated ESR and CRP",
            "Pale swollen optic disc (AION)",
            "Polymyalgia rheumatica symptoms",
        ],
        differential=[
            "Non-arteritic anterior ischaemic optic neuropathy",
            "Central retinal artery occlusion",
            "Optic neuritis",
            "Takayasu arteritis",
        ],
    ),
    CaseVignette(
        id="eval-004",
        title="Young man with recurrent abdominal pain",
        presentation=(
            "A 22-year-old man of Eastern Mediterranean descent presents with "
            "recurrent episodes of severe abdominal pain, fever (39.2°C), and "
            "pleuritic chest pain. Episodes last 1-3 days and resolve "
            "spontaneously. He has had approximately 15 similar episodes over "
            "the past 4 years. Between episodes he is completely well. His "
            "father and paternal uncle have similar symptoms. During the current "
            "episode: abdomen is diffusely tender with guarding but no rebound, "
            "a left-sided pleural friction rub is present. Labs show WBC 15,000, "
            "CRP 120 mg/L, ESR 65 mm/hr. Serum amyloid A is markedly elevated. "
            "Previous workups including CT abdomen, endoscopy, and laparoscopy "
            "during an acute episode showed only sterile peritoneal inflammation."
        ),
        gold_diagnosis="Familial Mediterranean Fever",
        gold_aliases=["FMF", "familial mediterranean fever"],
        difficulty="moderate",
        source="Classic teaching case",
        key_findings=[
            "Recurrent self-limited febrile episodes",
            "Serositis (peritonitis and pleurisy)",
            "Eastern Mediterranean descent",
            "Family history of similar episodes",
            "Elevated serum amyloid A (AA amyloidosis risk)",
            "Sterile peritoneal inflammation",
        ],
        differential=[
            "Acute intermittent porphyria",
            "Hereditary angioedema",
            "Periodic fever syndromes (TRAPS, HIDS)",
            "Crohn disease",
            "Systemic lupus erythematosus",
        ],
    ),
    CaseVignette(
        id="eval-005",
        title="Woman with weight gain, hypertension, and bruising",
        presentation=(
            "A 35-year-old woman presents with a 12-month history of progressive "
            "weight gain (15 kg), predominantly truncal, with facial rounding. "
            "She has developed purple striae on her abdomen, easy bruising, and "
            "proximal muscle weakness. She reports depression, amenorrhoea for "
            "6 months, and new-onset hypertension requiring two medications. "
            "She takes no exogenous steroids. On examination: moon facies, "
            "dorsocervical fat pad, central obesity with thin extremities, "
            "wide (>1 cm) violaceous abdominal striae, and proximal weakness. "
            "Screening tests: 24-hour urinary free cortisol is 4x upper limit "
            "of normal, late-night salivary cortisol is elevated on two "
            "occasions, and 1 mg overnight dexamethasone suppression test fails "
            "to suppress morning cortisol. ACTH is elevated at 85 pg/mL."
        ),
        gold_diagnosis="Cushing disease (ACTH-secreting pituitary adenoma)",
        gold_aliases=[
            "Cushing disease",
            "Cushing's disease",
            "ACTH-dependent Cushing syndrome",
            "pituitary Cushing syndrome",
        ],
        difficulty="moderate",
        source="Classic teaching case",
        key_findings=[
            "Truncal obesity with thin extremities",
            "Moon facies and dorsocervical fat pad",
            "Wide violaceous striae",
            "Elevated 24h urinary free cortisol",
            "Failed dexamethasone suppression",
            "Elevated ACTH (ACTH-dependent)",
            "No exogenous steroid use",
        ],
        differential=[
            "Ectopic ACTH syndrome",
            "Adrenal adenoma/carcinoma",
            "Pseudo-Cushing syndrome (depression/alcohol)",
            "Metabolic syndrome",
            "Polycystic ovary syndrome",
        ],
    ),
    CaseVignette(
        id="eval-006",
        title="Child with recurrent infections and eczema",
        presentation=(
            "A 3-year-old boy is referred for recurrent sinopulmonary infections "
            "since infancy, including 4 episodes of otitis media and 2 "
            "pneumonias in the past year. He also has severe eczema since age "
            "2 months and a history of prolonged bleeding after circumcision. "
            "His maternal uncle died at age 8 from intracranial haemorrhage. "
            "Physical examination reveals diffuse eczematous dermatitis, "
            "petechiae on the lower extremities, and mild hepatosplenomegaly. "
            "Laboratory: WBC 9,500 with normal differential, platelets 35,000 "
            "with notably small platelets on peripheral smear (low MPV), IgM is "
            "low, IgA and IgE are elevated, IgG is normal. Flow cytometry shows "
            "absent WASP expression in lymphocytes."
        ),
        gold_diagnosis="Wiskott-Aldrich Syndrome",
        gold_aliases=["WAS", "Wiskott-Aldrich syndrome"],
        difficulty="hard",
        source="Classic teaching case",
        key_findings=[
            "Triad: eczema, thrombocytopenia, immunodeficiency",
            "Small platelets (low MPV) — characteristic",
            "X-linked pattern (maternal uncle)",
            "Recurrent sinopulmonary infections",
            "Absent WASP expression",
            "Elevated IgA and IgE, low IgM",
        ],
        differential=[
            "Immune thrombocytopenic purpura (ITP)",
            "Atopic dermatitis with coincidental thrombocytopenia",
            "X-linked agammaglobulinemia",
            "Hyper-IgE syndrome",
            "IPEX syndrome",
        ],
    ),
    CaseVignette(
        id="eval-007",
        title="Man with acute onset confusion and fever after camping",
        presentation=(
            "A 45-year-old man is brought to the emergency department in July "
            "with 5 days of fever, headache, myalgias, and progressive "
            "confusion. He returned from a camping trip in the Southeastern "
            "United States 10 days ago and recalls multiple tick bites. On "
            "examination: temperature 39.8°C, confused and disoriented, "
            "a diffuse maculopapular rash involving palms and soles is noted, "
            "which his wife says started on his wrists and ankles 2 days ago "
            "before spreading centrally. Labs: WBC 3,800, platelets 95,000, "
            "sodium 129 mEq/L, AST 180, ALT 145, LDH elevated. CSF shows mild "
            "lymphocytic pleocytosis."
        ),
        gold_diagnosis="Rocky Mountain Spotted Fever",
        gold_aliases=[
            "RMSF",
            "Rocky Mountain spotted fever",
            "Rickettsia rickettsii infection",
        ],
        difficulty="moderate",
        source="Classic teaching case",
        key_findings=[
            "Tick exposure in endemic region",
            "Fever, headache, confusion (meningoencephalitis)",
            "Rash spreading centripetally from extremities",
            "Rash involving palms and soles",
            "Thrombocytopenia and hyponatremia",
            "Elevated transaminases",
        ],
        differential=[
            "Ehrlichiosis / anaplasmosis",
            "Meningococcemia",
            "Secondary syphilis",
            "Leptospirosis",
            "Viral meningoencephalitis",
        ],
    ),
    CaseVignette(
        id="eval-008",
        title="Woman with dyspnoea and a mediastinal mass",
        presentation=(
            "A 30-year-old woman presents with 2 months of progressive dyspnoea, "
            "dry cough, and a 10 kg weight loss. She reports night sweats and "
            "intermittent pruritus. She has noticed a painless swelling in her "
            "left neck for 3 weeks. On examination: a 4 cm firm, rubbery, "
            "non-tender left supraclavicular lymph node and diminished breath "
            "sounds at the right base. Chest CT shows a large anterior "
            "mediastinal mass (12 cm) with multiple enlarged mediastinal lymph "
            "nodes and a moderate right pleural effusion. FDG-PET shows intense "
            "uptake in the mediastinal mass and bilateral cervical, axillary, "
            "and para-aortic nodes. Excisional biopsy of the supraclavicular "
            "node shows effacement of architecture by large binucleated cells "
            "with prominent eosinophilic nucleoli (Reed-Sternberg cells) in a "
            "mixed inflammatory background. Immunohistochemistry: CD15+, CD30+, "
            "CD45-."
        ),
        gold_diagnosis="Classic Hodgkin Lymphoma (nodular sclerosis subtype)",
        gold_aliases=[
            "Hodgkin lymphoma",
            "Hodgkin's lymphoma",
            "Hodgkin disease",
            "classical Hodgkin lymphoma",
            "nodular sclerosis Hodgkin lymphoma",
        ],
        difficulty="easy",
        source="Classic teaching case",
        key_findings=[
            "B symptoms (weight loss, night sweats)",
            "Anterior mediastinal mass in young woman",
            "Reed-Sternberg cells on biopsy",
            "CD15+, CD30+, CD45- immunophenotype",
            "Contiguous lymph node involvement",
        ],
        differential=[
            "Primary mediastinal B-cell lymphoma",
            "Thymoma",
            "Germ cell tumour",
            "Sarcoidosis",
            "Non-Hodgkin lymphoma",
        ],
    ),
    CaseVignette(
        id="eval-009",
        title="Infant with failure to thrive and steatorrhoea",
        presentation=(
            "A 9-month-old boy is brought in for poor weight gain and frequent "
            "bulky, foul-smelling stools since introduction of solid foods. "
            "Birth weight was normal but he has fallen from the 50th to the 5th "
            "percentile for weight. He has had two episodes of bronchiolitis and "
            "one pneumonia. Parents note his skin tastes salty when kissed. "
            "On examination: thin, irritable infant with mild abdominal "
            "distension and wasted buttocks. A rectal prolapse was noted once "
            "during straining. A sweat chloride test returns 78 mEq/L (repeated "
            "at 82 mEq/L). Faecal elastase is very low (<50 μg/g)."
        ),
        gold_diagnosis="Cystic Fibrosis",
        gold_aliases=[
            "CF",
            "cystic fibrosis",
            "mucoviscidosis",
        ],
        difficulty="easy",
        source="Classic teaching case",
        key_findings=[
            "Failure to thrive with steatorrhoea",
            "Recurrent respiratory infections in infancy",
            "Salty-tasting skin",
            "Elevated sweat chloride (>60 mEq/L)",
            "Low faecal elastase (pancreatic insufficiency)",
            "Rectal prolapse",
        ],
        differential=[
            "Coeliac disease",
            "Shwachman-Diamond syndrome",
            "Primary ciliary dyskinesia",
            "Cow's milk protein intolerance",
            "Immunodeficiency",
        ],
    ),
    CaseVignette(
        id="eval-010",
        title="Man with acute flank pain and haematuria",
        presentation=(
            "A 38-year-old man presents to the emergency department with sudden "
            "onset severe right flank pain radiating to the groin, associated "
            "with nausea and haematuria. He has had two similar but milder "
            "episodes in the past 5 years. He also reports a history of recurrent "
            "calcium kidney stones. On further questioning, he describes chronic "
            "fatigue, constipation, and frequent urination. He was recently told "
            "his blood pressure was elevated (150/95). Labs: calcium 12.2 mg/dL "
            "(elevated), phosphate 2.1 mg/dL (low), PTH 145 pg/mL (markedly "
            "elevated, normal 10-65), 24h urine calcium elevated, creatinine "
            "normal, vitamin D normal. CT abdomen shows a 6 mm right ureteric "
            "stone and bilateral nephrocalcinosis. Sestamibi scan shows a focus "
            "of increased uptake posterior to the right inferior thyroid pole."
        ),
        gold_diagnosis="Primary Hyperparathyroidism (parathyroid adenoma)",
        gold_aliases=[
            "primary hyperparathyroidism",
            "hyperparathyroidism",
            "parathyroid adenoma",
            "PHPT",
        ],
        difficulty="moderate",
        source="Classic teaching case",
        key_findings=[
            "Hypercalcaemia with elevated PTH",
            "Recurrent calcium kidney stones",
            "Nephrocalcinosis",
            "Low phosphate",
            "Sestamibi-positive lesion at inferior thyroid pole",
            "'Stones, bones, groans, and moans' symptoms",
        ],
        differential=[
            "Familial hypocalciuric hypercalcaemia",
            "Malignancy-associated hypercalcaemia (PTHrP)",
            "Vitamin D excess",
            "Sarcoidosis",
            "Multiple endocrine neoplasia (MEN1/2A)",
        ],
    ),
]


def get_case(case_id: str) -> CaseVignette | None:
    """Look up a case by ID."""
    for c in CASES:
        if c.id == case_id:
            return c
    return None


def get_cases_by_difficulty(difficulty: str) -> list[CaseVignette]:
    """Filter cases by difficulty level."""
    return [c for c in CASES if c.difficulty == difficulty]
