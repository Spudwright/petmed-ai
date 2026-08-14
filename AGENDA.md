# crittr — the model, and the agenda

_Last updated 2026-08-13._

---

## The model, in one paragraph

**crittr is the digital health layer between a pet owner and the veterinarian they already
have.** An owner arrives at 2am with a worried question. crittr triages, and routes them to
a real vet — ideally *their* vet. That vet has already examined the animal in person, which
is what establishes the VCPR, which is what makes everything afterwards legal telemedicine
in even the strictest states. From there crittr runs the part nobody else covers: the care
plan, whether the doses were actually given, the follow-up, the refill request routed back
to the prescribing vet — and the practice earns on what its own clients buy.

**The regulated part is the in-person visit. Everything downstream of it is ours.**

## What we are not

| | Their lane | Why we don't enter it |
|---|---|---|
| **Vetster** | Consumer → a *new* online vet | They must create a VCPR from nothing. We inherit one. Our constraint is our moat. |
| **Vetsource** | Practice → pharmacy & store, PIMS-integrated | They have licensed pharmacy, thousands of SKUs, warehouses. We have one SKU and no 3PL. Unwinnable and unnecessary. |
| **Chewy** | Everything, cheapest | Not a fight. Not a category we want. |

**Directive (MBD, 2026-08-13): front-line focus on our own model. Do not compete with
Vetster or Vetsource.** Partner for fulfilment; never rebuild it.

## The two things nobody else has

1. **A consumer front door.** The 2am triage is the acquisition engine, not a feature.
   Vetsource can only ever reach clients a practice sends it.
2. **The at-home record.** `NONE of 20 doses marked given`. Vetsource knows what shipped.
   It has no way to know what happened. A fulfilment company has no reason to build this.

## The capital principle

**Simple core, expansion funded by core revenue.** No large up-front commitments, no
inventory bet ahead of demand, no infrastructure we can partner for. Every expansion pays
for the next one.

> This revises an earlier recommendation. I suggested launching five own-label SKUs
> (~$50–70k of inventory). Under a grow-from-revenue rule that is the wrong shape.
> **Start with one SKU at minimum MOQ, prove the reorder rate, then add the second from
> its margin.**

---

## Built and live

| | Status |
|---|---|
| Vet portal — accounts, licence verification, case queue, audit log | ✅ |
| Per-state compliance — default deny, activated by a named human | ✅ |
| VCPR records + prescribing gate (state × licence × live VCPR) | ✅ |
| Aftercare — care plans, dose tracking, follow-ups, refill routing | ✅ |
| Practice client book — CSV import, attestation, invite, claim, release | ✅ |
| Revenue share — flat 10%, net of discounts, refund reversal, payout-aware clawback | ✅ |
| Stripe Connect Express payouts — admin-only, idempotent, 14-day holdback | ✅ code |
| Chart assistant `/vet/patients` — gated to own clients, every read audited | ⚠️ untested live |
| `/admin/readiness` — names silent failures, live-probes Resend | ✅ |
| `/admin/products` — cost, margin, share affordability, sale path | ✅ |
| `/for-vets` — recruitment page, honest economics, Vetsource objection | ✅ |

## Next — mine

1. **Verify the chart assistant actually answers on production.** It is what `/for-vets`
   leads with and it has never been seen producing a real answer (no LLM key in the test
   environment). Nothing else ships until this is watched working once.
2. **Per-state gate on the practice layer.** Routing and prescribing are default-deny per
   state; the client book is not. Defensible today, awkward to explain later.
3. **The walkthrough** — one hosted link, refreshed in place, once the above is done.

## Next — yours

1. **Enable Connect in the Stripe dashboard.** Account creation refuses until it is on, so
   the payout path is untested end to end.
2. **Open `/admin/readiness`** with admin credentials. If Resend reads *set* but not
   *valid*, every invitation silently vanishes.
3. **One own-label SKU.** Joint is the strongest candidate — highest-priced affiliate
   category, the most common vet recommendation, easiest format to co-pack.
   **Supplier shortlist below — contact Pet Tech Labs and Bimini.**

## Supplier shortlist (researched 2026-08-13)

**Recommended: Pet Tech Labs**, a FoodScience company. FoodScience owns **VetriScience**
(40 years in the veterinary channel) and **Pet Naturals**, and acquired Pet Tech Labs in
2021 as its contract-manufacturing arm. crittr already sells VetriScience Nu Cat — so the
line to a vet is "made by the same manufacturer as VetriScience," which solves the
credibility problem a new supplement brand has in this channel.

| | Pet Tech Labs | Bimini Pet Health | Garmon (Swedencare) |
|---|---|---|---|
| MOQ, soft chews | **504 units** | "ultra-low" on some formulas | higher — scale player |
| Certifications | NASC, NSF, SQF, cGMP | NASC Preferred, cGMP, FDA, USDA/APHIS | NASC Preferred, cGMP |
| Turnkey formulas | dozens | yes, standard packaging | yes |
| Fulfilment / dropship | not offered — needs a 3PL | **lists Fulfilment & Logistics** | not confirmed |

**Capital, revised down.** 504 units at ~$6 landed ≈ **$3,000** for a first run, against
~$15,000 revenue at $29.99 full sell-through. This replaces the earlier ~$50–70k estimate,
which assumed five SKUs at 2,000 units — the wrong shape under a grow-from-revenue rule.

**Do not use generic dropship platforms** (Supliful, CJ, Eprolo). Zero inventory is
tempting and it is the wrong trade: they are not NASC, and a vet will not recommend a
supplement without the seal. For a brand distributed by veterinarians, NASC *is* the
product.

**The structural finding:** private label + true dropship + NASC barely exists as a
combination. NASC manufacturers ship bulk; dropship platforms aren't NASC. The shape is
therefore *NASC manufacturer + 3PL* — which is why `dropship.py` is supplier-agnostic with
an email-PO route as the default.

**Three questions for each:**
1. MOQ and landed cost for a joint soft chew, 60-count jar, at 504 and 1,000 units.
2. Will you fulfil direct to consumer, or ship bulk to a 3PL? (Decides whether a second
   vendor is needed at all — Bimini may cover both.)
3. Who holds the state feed registrations, and can we use your NASC membership on the
   label or do we need our own? (Most-missed question; costs the most later.)

## Fulfilment — decided 2026-08-13

**Manufacturer makes it once; a 3PL posts it forever.** Two companies, two jobs.

- **Make:** Pet Tech Labs (Nebraska), 504 units, ~$3,528. Reorder lead time ~8-12 weeks on
  an existing formula; Nebraska to Houston is 2-4 days LTL. **Plan ~3 months door to door.**
- **Post:** KAK Sourcing (Houston, TX) — $2.75/order, $25/pallet biweekly (~$54/mo),
  no setup fee, no minimum, no peak surcharge. Central for national postage.
- **Register for sales tax in Texas only.** Stock location creates nexus; nothing owed
  elsewhere until ~$100k into a single state. Sales tax is never crittr's money.
- **Reorder trigger is a SIGNATURE, not a shelf level.** 504 jars is 12 months at 42
  orders/month but only 5 WEEKS if one practice with 400 clients signs — against a 3-month
  lead time. Reorder the day a clinic signs. Ask about blanket/standing orders.

**Rejected: Amazon MCF.** Aged-inventory surcharges ($1.50/cu ft at 181 days, $3.80 at 271,
$6.90 at 365) punish exactly a 12-month sell-through. KAK's flat $54/month does not care how
long stock sits. Revisit if stock ever turns in under 3 months.

**Rejected for now: selling ON Amazon.** It breaks the model — no crittr order means no
attribution, so the vet earns nothing, and crittr loses the customer, the repeat purchase
and the adherence record. It is the affiliate problem again with our own product.

⏳ **But it is the agreed Plan B.** crittr has no traffic; Amazon's 15% is the price of not
having to find buyers. **If no practice signs within ~90 days of stock landing, list on
Amazon** — 15% of something beats 100% of inventory that expires at 18 months.

## Unit economics — CRITTR Joint @ $39.99

| | Per jar | 504 sold out |
|---|---|---|
| Net, direct | $20.49 | **~$10,300 (51%)** |
| Net, via a practice | $16.49 | **~$8,300 (41%)** |

Cash needed up front: **$3,528 — the stock only.** Postage, pick-and-pack and Stripe come
out of each sale after the customer has paid. ⚠ The $7 product cost is an ESTIMATE and the
only unverified number in the model — at $9 the margin is 34%, at $5 it is 47%.

## Open decisions

- **Rx compensation.** Pay the vet a telehealth consult fee, *not* a share of a drug they
  prescribed. Product share stays OTC-only. Two ledgers, cleanly separated.
- **Rx share basis.** If crittr takes a margin from a pharmacy partner rather than owning
  the sale, the vet's share must be a % of *crittr's net revenue*, not the retail price —
  or a $200 refill pays $30 out of maybe $20 of margin.
- **PIMS integration.** Vetsource syncs; we take a CSV snapshot that goes stale the next
  day. ezyVet (REST API) is the tractable first one. Cornerstone and AVImark are
  on-premise and much harder.
- **CRITTR Calm is `in_stock` with no supplier.** A customer can pay $29.99 today for
  something that cannot ship. `crittr_calm_compliance.set_stock(q, False)` is one call.

## Compliance — known, not yet resolved

- ✅ **Fixed 2026-08-13:** hemp removed (not an AAFCO-accepted feed ingredient),
  "veterinarian-formulated" removed (not true yet), "anxiety" removed from copy and tags
  (a condition claim makes a supplement an unapproved drug).
- ⬜ **State feed registrations** — most states require per-product registration with the
  state feed control official, with annual fees. A matrix, not one filing.
- ⬜ **NASC Quality Seal** — voluntary, but it is what a vet checks for. Insist the
  co-packer can produce NASC-compliant product, cGMP, and a COA per batch.
- ⬜ **Full AAFCO label** — ingredient list and complete guaranteed analysis on the back
  panel; the front-panel mockup is not a label.
- ⬜ **A vet formulator** — needed before "veterinarian formulated" goes back on, and
  worth having regardless for a product sold into the veterinary channel.
- ⚠️ **Not legal advice.** This needs counsel who actually practises FDA/AAFCO work.
