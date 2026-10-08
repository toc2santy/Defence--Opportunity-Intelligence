-- ============================================================
-- Keyword-only matching (2026-10).
--
-- Until now a tender became a CANDIDATE for a product only if its
-- classification code was mapped to one of the product's capabilities;
-- keywords only re-scored it. So a tender titled "Watercraft Spare Parts"
-- or "Multi Party Framework for Simulators" under an unmapped code was
-- invisible to every product, however plainly it described one.
--
-- Simulated on the 11,565 stored tenders (2026-10-06): ~800 tenders reach a
-- keyword score >= 3 without a mapped code, ~500 of them in NO capability's
-- code set at all. But quality is uneven: spares/weapons/training titles were
-- almost all right, while a lone generic word ("surveillance", "naval") pulled
-- in civilian CCTV and unrelated Spanish tyre tenders. A threshold alone cannot
-- separate those, so precision is controlled by curation instead:
--
--   standalone = true  marks a keyword as strong enough to make a tender a
--                      candidate ON ITS OWN. Only a human sets it (the
--                      learner of migration 052 never does). Default false,
--                      so nothing changes until a keyword is flagged.
--
-- First batch: SPARES.REPLACEMENT and WEAPONS.AMMUNITION from migration 053
-- (TRAINING.SIMULATION is deliberately NOT flagged, see below). Short acronyms
-- ('uav', 'uas') and generic words ('weapons', 'cartridge', 'simulation',
-- 'training aid') are deliberately NOT flagged.
--
-- TIGHTENED after a precision read of the first sample (real stored tenders):
--   * English 'ammunition' / 'munition(s)' / 'small arms' / 'explosive
--     ordnance' are NOT flagged: in English titles they mostly name a PLANT or a
--     SERVICE ("Radford Army Ammunition Plant ... Storage Tanks", "Munitions
--     Testing and Engineering Services", an EOD response vehicle) — roughly half
--     of that sample was wrong. The foreign-language forms ('munición',
--     'amunicji', 'střelivo'...) were all right and stay flagged. English
--     ammunition supply tenders are still found through the mapped CPV 353xx codes.
--   * bare 'simulator'/'simulators' are NOT flagged: they pulled in an
--     "Ultrasound Simulator", GNSS/radar-signal test simulators and, via
--     'sprzętu symulacyjnego', medical training dolls. Only training-specific
--     phrases ('flight simulator', 'tactical trainer', 'firing range'...) are.
--   * SPARES stays fully flagged: every sampled title was a genuine spare-parts
--     tender.
--   * TRAINING.SIMULATION has NO standalone keyword at all: in the reviewed sample
--     all 4 keyword-only hits were wrong (a civil FAA airliner flight simulator,
--     a software update for a tactical trainer, maintenance of simulator IT
--     equipment). Training tenders are still found through the mapped codes.
--   * Reviewed by a person (2026-10-08): 40 of 46 sampled rows right = 87%
--     (spares 33/34, weapons 7/8, training 0/4).
--
-- opportunities.match_basis records HOW a tender matched so the UI can say
-- "title keyword match, no classification code" instead of passing it off as a
-- code-backed match.
-- ============================================================

alter table capability_taxonomy_keywords
    add column standalone boolean not null default false;

comment on column capability_taxonomy_keywords.standalone is
    'true = this keyword alone may make a tender a match candidate (keyword-only matching, app/programme_matching.py). Set by a person only.';

alter table opportunities
    add column match_basis text not null default 'code'
        check (match_basis in ('code', 'keyword'));

comment on column opportunities.match_basis is
    'code = matched through a classification-code mapping (keywords may corroborate); keyword = matched on standalone title keywords only, no mapped code. Keyword-only matches are capped at medium confidence.';

update capability_taxonomy_keywords k
set standalone = true
from capability_taxonomy ct
where ct.id = k.capability_id
  and (ct.code, k.keyword) in (
    -- SPARES.REPLACEMENT
    ('SPARES.REPLACEMENT', 'spare part'), ('SPARES.REPLACEMENT', 'spare parts'), ('SPARES.REPLACEMENT', 'spares'),
    ('SPARES.REPLACEMENT', 'replacement part'), ('SPARES.REPLACEMENT', 'replacement parts'),
    ('SPARES.REPLACEMENT', 'repair parts'), ('SPARES.REPLACEMENT', 'repuesto'), ('SPARES.REPLACEMENT', 'repuestos'),
    ('SPARES.REPLACEMENT', 'náhradní díly'), ('SPARES.REPLACEMENT', 'náhradných dielov'), ('SPARES.REPLACEMENT', 'náhradné diely'),
    ('SPARES.REPLACEMENT', 'ersatzteil'), ('SPARES.REPLACEMENT', 'ersatzteile'),
    ('SPARES.REPLACEMENT', 'pièces de rechange'), ('SPARES.REPLACEMENT', 'pièce de rechange'),
    ('SPARES.REPLACEMENT', 'części zamienne'), ('SPARES.REPLACEMENT', 'запчастини'), ('SPARES.REPLACEMENT', 'запасні частини'),
    -- WEAPONS.AMMUNITION
    ('WEAPONS.AMMUNITION', 'firearm'), ('WEAPONS.AMMUNITION', 'firearms'),
    ('WEAPONS.AMMUNITION', 'machine gun'), ('WEAPONS.AMMUNITION', 'machine guns'),
    ('WEAPONS.AMMUNITION', 'grenade'), ('WEAPONS.AMMUNITION', 'grenades'),
    ('WEAPONS.AMMUNITION', 'munición'), ('WEAPONS.AMMUNITION', 'municiones'), ('WEAPONS.AMMUNITION', 'granadas'),
    ('WEAPONS.AMMUNITION', 'armes à feu'), ('WEAPONS.AMMUNITION', 'munitionen'), ('WEAPONS.AMMUNITION', 'feuerwaffen'),
    ('WEAPONS.AMMUNITION', 'schusswaffen'), ('WEAPONS.AMMUNITION', 'střelivo'), ('WEAPONS.AMMUNITION', 'amunicja'),
    ('WEAPONS.AMMUNITION', 'amunicji'), ('WEAPONS.AMMUNITION', 'broń palna'), ('WEAPONS.AMMUNITION', 'боєприпаси'),
    ('WEAPONS.AMMUNITION', 'набої'), ('WEAPONS.AMMUNITION', 'гранати'), ('WEAPONS.AMMUNITION', 'стрілецька зброя')
  );
