# Agent evaluation · GitLab GTM Ops

29 questions × 3 trial(s) × 3 system(s), model `openai/gpt-4.1` (judge `openai/gpt-4.1`), LLM cache off. Run 2026-10-17 21:14. Questions: `eval/gitlab-gtm/questions.jsonl`.

Pass rate = share of all trials that pass; pass^k = share of questions that pass in every trial (consistency).

| Category | Q | handbookbrain pass | handbookbrain pass^3 | plain_rag pass | plain_rag pass^3 | rag_plus_gitlog pass | rag_plus_gitlog pass^3 |
|---|---|---|---|---|---|---|---|
| Why / who decided | 6 | 100% | 100% | 0% | 0% | 17% | 17% |
| Versions over time | 5 | 87% | 80% | 40% | 40% | 60% | 60% |
| Decided, never documented | 3 | 100% | 100% | 100% | 100% | 100% | 100% |
| Employee questions (lookup) | 10 | 70% | 70% | 70% | 70% | 70% | 70% |
| Not in the docs (must abstain) | 4 | 100% | 100% | 100% | 100% | 100% | 100% |
| Only said in chat | 1 | 100% | 100% | 100% | 100% | 100% | 100% |
| **All** | 29 | 87% | 86% | 59% | 59% | 66% | 66% |

- **handbookbrain**: 2.81 USD, 4.7 min wall clock, 6.1 s per answer; lookups citing the exact gold section 90%, judged partial 20%; wrongly said 'not documented' 8×
- **plain_rag**: 0.33 USD, 1.6 min wall clock, 1.9 s per answer; lookups citing the exact gold section 80%, judged partial 10%; wrongly said 'not documented' 30×
- **rag_plus_gitlog**: 0.42 USD, 1.6 min wall clock, 1.9 s per answer; lookups citing the exact gold section 80%, judged partial 10%; wrongly said 'not documented' 27×

Facts found, all trials (why: MR / meeting date / person or meeting; version: per required fact):

- handbookbrain: version:fact1 15/15, version:fact2 3/3, why:MR 18/18, why:date 18/18, why:who/where 18/18
- plain_rag: version:fact1 6/15, version:fact2 0/3, why:MR 0/18, why:date 18/18, why:who/where 14/18
- rag_plus_gitlog: version:fact1 9/15, version:fact2 3/3, why:MR 4/18, why:date 18/18, why:who/where 15/18

## Failures (first trial), to read

### handbookbrain
- `look-1` judge: wrong (Contradicts REFERENCE by saying the handbook doesn't say; REFERENCE provides full instructions including doc attachment guidance.) abstained
  > The handbook doesn't say.  However, according to a Slack conversation, to request support from another team (such as the pricing team) in Salesforce, you should go to the account or opportunity page. There is a "Request Support" button at the top—click that, select the team you need, and fill out the form that appears. The conversation does not specify whether you must attach documents, but it is 
- `look-2` judge: partial (Correctly identifies Internal Incentive Guide as source and that discounts differ, but invents Partner Portal details not in REFERENCE.)
  > Discounts and incentives for resellers, distributors, and MSPs are detailed in the following resources:  - The **GitLab Program Discounts and Incentive Guide** (for partners, available in the GitLab Partner Portal) - The **Internal Incentive Guide** (for GitLab employees)  These guides contain the specific discount levels, incentives, and rebate programs for each partner type. This is explicitly s
- `look-9` judge: partial (Includes correct process and timeline, but adds steps and approvals not in the REFERENCE; omits DRI details.)
  > The review process and timeline for new customer success program content are:  - The requester submits a proposal issue using the correct template, including as much information as possible and the desired date for the program to go to customers. If the form is incomplete, this may delay completion. The CS Programs team verifies the information and may request clarification. The requester provides

### plain_rag
- `why-1` missing ['20534'] abstained
  > The handbook doesn't say why the Marketo trial MQL threshold was changed from 25 to 31 points or who specifically decided it. The change is mentioned in the Marketing Ops weekly meeting, where Rachel Kim recaps the update and asks for objections, indicating it was discussed and agreed upon by the Marketing Operations team during that meeting, but no reason is provided in the available context (see
- `why-2` missing ['19600']
  > Trial threshold scoring was added to the Marketo handbook page to provide a documented section for how GitLab MQLs trial users based on specific characteristics, working in unison with the original lead scoring workflows. This addition was discussed and approved in the Marketing Ops weekly meeting on 2026-05-08, where it was noted that there wasn't a documented section yet and that Diego would dra
- `why-3` missing ['20248', 'Ken Watanabe']
  > Channel partner deals are no longer auto-renewed because the partner holds the commercial relationship with the end customer. Renewal decisions on indirect (partner-transacted) deals should be managed through the partner, not enforced by GitLab directly. This means partners and end customers are not obligated to renew solely because a cancellation notice was not submitted within the standard 30-da
- `why-4` missing ['20005'] abstained
  > The handbook doesn't say why the Initial Source logic for renewal opportunities changed or who made the call. However, a Channel Ops sync conversation shows the team discussed that the Initial Source for renewals will always reference the oldest Closed-Won, New Biz opportunity on the account, not the linked source opportunity (Ken Watanabe, Channel Ops sync, teams, 2026-06-03T09:31:35Z). The conve
- `why-5` missing ['15742'] abstained
  > The handbook doesn't say why the partner sourced deal registration process for resale and referral deals changed. However, a conversation from the Channel Ops sync on 2026-05-22 indicates that the process changes were timely due to an increase in partner-sourced deals, and the new process language for both resale and referral deal registrations was approved by the team, with the ESM review section
- `why-6` missing ['20450']
  > The 'MktgOps::08: Completed' label was added to clarify when MktgOps has completed their task on an issue, even if the issue itself is not closed. The decision to add this label was made by the Marketing Ops team, as discussed and agreed upon in their weekly meeting (specifically by Rachel Kim and Diego Santos) [marketing/marketing-operations/_index#labeling-part-2] (conversation: Marketing Ops we
- `ver-2` missing ['25', 'Jul 23'] abstained
  > The handbook doesn't say what the Marketo trial MQL threshold was before it was last changed, nor when the change occurred. There is no information in the provided context specifying the previous threshold or the date of the change.
- `ver-4` missing ['15742'] abstained
  > The handbook doesn't say.
- `ver-5` missing ['Jun 30'] abstained
  > The handbook doesn't say when the answer about auto-renewal on partner-transacted deals was added to the FAQ.
- `look-1` judge: wrong (The REFERENCE provides a clear workflow for requesting support from another team in Salesforce.) abstained
  > The handbook doesn't say.
- `look-3` judge: wrong (Contradicts REFERENCE; handbook explicitly says you can send food gifts and browse options without starting a campaign.) abstained
  > The handbook doesn't say whether you can send food gifts like chocolates or flowers through Reachdesk, nor does it mention if you can browse the marketplace options without starting a campaign.
- `look-9` judge: partial (Covers review steps and external review, but omits key approval by marketing/SME and the 10 working day timeline.)
  > The review process for new customer success program content is as follows:  - Step 3: Define call to action (Program Manager, 1-2 days) - Step 4: Create email copy (Program Manager or GitLab Team Member SME, 3-5 days) - Step 5: Assign a template to email copy  Creating new content or adapting existing content requires review outside of the Digital Customer Programs team. Content must be reviewed f

### rag_plus_gitlog
- `why-1` missing ['20534'] abstained
  > The handbook doesn't say why the Marketo trial MQL threshold was increased from 25 to 31 points or who specifically decided it. However, meeting notes from the Marketing Ops weekly (2026-07-17) show that the team discussed and finalized the update, with Rachel Kim confirming the change and asking for objections, indicating it was a Marketing Operations team decision [Marketing Ops weekly, zoom, 20
- `why-3` missing ['20248']
  > Channel partner deals are no longer auto-renewed because the partner holds the commercial relationship with the end customer, and renewal decisions on indirect deals should be managed through the partner, not enforced by GitLab directly. This means partners and end customers are not obligated to renew solely because a cancellation notice was not submitted within the standard 30-day window. The pol
- `why-4` missing ['20005'] abstained
  > The handbook doesn't say why the Initial Source logic for renewal opportunities changed or who specifically made the call. However, a Channel Ops sync conversation shows that the main change is that the Initial Source for renewals now always references the oldest Closed-Won, New Biz opportunity on the account, not the linked source opportunity (Ken Watanabe, Channel Ops sync, teams, 2026-06-03T09:
- `why-5` missing ['15742', 'Ken Watanabe'] abstained
  > The handbook doesn't say why the partner sourced deal registration process for resale and referral deals changed. However, a conversation from the Channel Ops sync on 2026-05-22 indicates that the process changes were timely due to an increase in partner-sourced deals, and the new process language for both resale and referral deal registrations was approved by the team, with the ESM review section
- `why-6` missing ['20450']
  > The 'MktgOps::08: Completed' label was added to clarify when MktgOps has completed their task on an issue, even if the issue itself is not closed. The decision to add this label was made by the Marketing Ops team, as discussed in their weekly meeting, with Rachel Kim stating the decision and Diego Santos agreeing to update the handbook [marketing/marketing-operations/_index#labeling-part-2] (conve
- `ver-2` missing ['25'] abstained
  > The handbook doesn't say what the Marketo trial MQL threshold was before it was last changed. However, merge request !20534, merged on 2026-07-23, is titled "increase trial threshold," indicating the threshold was increased on that date. The exact previous threshold value is not specified in the provided context.
- `ver-4` missing ['15742'] abstained
  > The handbook doesn't say. There is no mention in the provided context of partner sourced deal registrations being auto-approved if nobody actions them within 10 days. The approval process described requires explicit review and action by the Ecosystem Sales Manager and Regional Director, but no auto-approval timeline is specified [sales/field-operations/channel-operations/_index#partner-sourced-dea
- `look-1` judge: wrong (The REFERENCE provides a clear workflow for requesting support from another team in Salesforce.) abstained
  > The handbook doesn't say.
- `look-3` judge: wrong (Contradicts REFERENCE; it is clear you can send food gifts and browse options without starting a campaign.) abstained
  > The handbook doesn't say whether you can send food gifts like chocolates or flowers through Reachdesk, nor does it mention if you can browse marketplace options without starting a campaign. The provided instructions only cover how to send a campaign or trigger sends through Salesforce, but do not specify the types of gifts available or browsing capabilities [marketing/marketing-operations/brillian
- `look-9` judge: partial (Covers review steps and approvals, but timeline is overstated (should be at least 10 working days, not 30-60 days).)
  > The review process for new customer success program content is as follows:  - Step 3: Define the call to action for each email (Program Manager, 1-2 days). - Step 4: Create or adapt email copy (Program Manager or GitLab Team Member SME, 3-5 days). - Step 5: Assign a template to the email copy.  Content must be reviewed for correctness, adherence to GitLab style, and readability. This review is don
