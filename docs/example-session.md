# Example session

This is a real turn from a live run against `claude-opus-5-5` (October 2026), lightly trimmed. Venues are fictional.

## Customer

> Planning a wedding for about 250 guests in Queens next June, ideally a Saturday. We want Indian food and need halal options. Budget around $60k.

## Agent activity (20 steps)

| # | Agent | Action |
|---|---|---|
| 1 | Concierge | Asked the Venue Scout |
| 2–12 | Venue Scout | Searched venues, read 2 venues' details, checked 8 hall/date combinations |
| 13 | Concierge | Showed venue cards: *Marigold Manor*, *Brightwater Club* |
| 14 | Concierge | Asked the Pricing Analyst |
| 15–19 | Pricing Analyst | Built 5 quotes |
| 20 | Concierge | Replied |

## Concierge reply

> Good news: I found a strong match, and it fits your budget easily.
>
> **1. Marigold Manor, Royal Hall (Jamaica Estates). Best fit**
> - **Food:** In-house Indian menu with halal options, plus vegetarian and Jain. It includes a live chaat counter, 3 curries, tandoor, biryani and a dessert bar.
> - **The hall:** Built for weddings and sangeets. It has a bridal suite, dance floor, stage, LED wall, valet parking and an entrance that suits a baraat.
> - **Dates:** All four June Saturdays are open, and the price is the same on each.
> - **Price:** **$44,747.62 all-in** for 250 guests, about $179 per guest. A 25% deposit of $11,186.91 is due when you confirm.
> - **One catch:** The room seats up to 600, so it's roomy for 250. The quote also doesn't list a bar, so bar service may cost extra.
>
> **2. Brightwater Club, Aegean Ballroom (Astoria). Backup**
> - **Food:** Halal options, but the menu is Greek, not Indian. It does include an open bar.
> - **Price:** $36,690.88 all-in.
> - **One catch:** It only works for you if they allow an outside Indian caterer, and that isn't confirmed.
>
> Would you like me to put a free 72-hour hold on Marigold Manor for one of those Saturdays? I'd just need your full name, email and preferred date.

## Checking the numbers

The Royal Hall quote, recomputed from the rules in [`services.py`](../banquet/services.py):

| Item | Amount |
|---|---|
| Rental | $9,000.00 |
| Saturday premium (25%) | $2,250.00 |
| June peak premium (15%) | $1,350.00 |
| Royal Feast, 250 × $95 | $23,750.00 |
| Service charge (20%) | $4,750.00 |
| **Subtotal** | **$41,100.00** |
| Sales tax (8.875%) | $3,647.625 |
| **Total** | **$44,747.625 → $44,747.62** |

The total in the reply matches exactly. Every figure in it came from `build_quote`, not from the model's own arithmetic.

## Notes

- The Scout's first search included the event date and hit a bug, since fixed and covered by a test. The Scout got the error back as a tool result, retried without the date, and checked availability separately. No human stepped in.
- The Concierge never mentioned ids, tools or the internal agents to the customer, as its prompt requires.
