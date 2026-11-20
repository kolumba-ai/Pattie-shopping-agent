# Context: Personal Agents and Amazon

Personal Agents Hackathon, San Francisco, November 20, 2026

This document has two parts. The first summarizes where the law stands on AI agents shopping on Amazon. The second sets the rules our personal agent follows when it browses and buys on Amazon for its owner.

## Part 1: The legal situation

**Short version:** the court order that blocked Perplexity's shopping agent from Amazon has been thrown out, but the lawsuit is still open. The ruling helps agents that act on a user's behalf under federal computer-access law. It does not settle Amazon's own terms of use.

> These points come from news and blog summaries, not from the court opinion or the docket. Treat the details as unverified until someone reads the primary sources.

### Timeline: *Amazon v. Perplexity*

| Date | What happened |
|---|---|
| March 9 or 10, 2026 (sources differ) | A federal district judge, reported as Maxine Chesney, issued an injunction blocking Perplexity's Comet agent from shopping on Amazon ([GeekWire](https://www.geekwire.com/2026/judge-blocks-perplexitys-ai-bot-from-shopping-on-amazon-in-early-test-of-agentic-commerce/)). |
| About one week later | The Ninth Circuit paused the injunction while the appeal was pending. |
| August 4, 2026 | The Ninth Circuit vacated the injunction. It reasoned that under the Computer Fraud and Abuse Act (CFAA), the human user, not the developer of the agent, is the one accessing the website ([Troutman Privacy](https://www.troutmanprivacy.com/2026/08/ninth-circuit-holds-human-user-not-developer-of-ai-agent-responsible-for-websites-accessed/)). |
| September 11, 2026 | Perplexity moved to dismiss Amazon's remaining federal and California computer-access claims ([TopDisputes](https://topdisputes.com/disputes/amazon-v-perplexity), [No Hacks](https://nohacks.co/blog/amazon-perplexity-cfaa-agent-visitor-rights)). No ruling on that motion has been found. |

### What the ruling does and doesn't cover

- **It's narrow.** Reports say it rests on the evidence at the preliminary-injunction stage and on how Comet specifically works, not on every agent visiting every site ([Cloudspress](https://www.cloudspress.com/the-perplexity-ai-and-amazon-clash-what-the-2026-court-ruling-means/)).
- **It isn't final.** Amazon could still ask for rehearing or appeal further, and the motion to dismiss is undecided.
- **It favors user-directed agents under federal computer-access law.** An agent browsing on its owner's instructions is treated as the owner accessing the site.
- **It doesn't settle contract issues.** Amazon's Conditions of Use and its requirement that agents identify themselves are separate from the CFAA question. Browsing as an agent without declaring it is where the remaining risk sits. The likely consequence is enforcement against the account, such as a suspension, rather than a court case against the owner.
- **Sources are mixed in quality.** Several are low-quality blogs, and the dates are inconsistent.

## Part 2: How our agent shops on Amazon

### Principle

The agent acts on behalf of its owner and shops the way its owner would. It uses the owner's own account, with the owner's own intent. It doesn't announce itself as an agent; it behaves like the person it represents. The owner accepts the risk described in Part 1.

The goal is not speed. The goal is to get the shopping done in the background, at the times the owner might plausibly shop, while the owner is working, eating or spending time with people.

### Rule 1: Know where the owner is

The agent must know the owner's current location and time zone. Every time window below is in the owner's **local time**. When the owner travels, the windows move with them.

### Rule 2: Shop more at normal hours, less at others

There are no hard on/off windows. Instead, a **shopping weight** from 0 to 1 says how likely the agent is to shop at each local time of day. It is implemented in [`shopping_hours.py`](shopping_hours.py).

- **Peaks:** the weight is 1.0 from 10:00 AM to 1:00 PM and from 7:00 PM to 10:00 PM.
- **Fading:** outside the peaks it falls off smoothly, following a bell curve (Gaussian) measured from the nearest peak edge. How fast it falls depends on the time of day:
  - **Morning, before 10 AM:** moderate fade. 9 AM is still likely, 7 AM is rare.
  - **Afternoon, between the peaks:** slow fade. It dips to about 0.3 around 4 PM, then rises again toward the evening.
  - **Night, after 10 PM:** fast fade. 11 PM is possible, and after midnight it's almost zero.
- **Off hours:** below a weight of **0.05** (roughly 12:30 AM to 7:15 AM), the agent doesn't start a session unless the owner asks for something urgent.

| Local time | Weight |
|---|---|
| 7:00 AM | 0.04 (off) |
| 8:00 AM | 0.25 |
| 9:00 AM | 0.71 |
| 10:00 AM to 1:00 PM | 1.00 |
| 2:00 PM | 0.88 |
| 3:00 PM | 0.61 |
| 4:00 PM | 0.32 |
| 5:00 PM | 0.61 |
| 6:00 PM | 0.88 |
| 7:00 PM to 10:00 PM | 1.00 |
| 11:00 PM | 0.46 |
| 11:30 PM | 0.17 |
| Midnight | 0.04 (off) |

**Choosing a start time:** the agent draws start times at random, in proportion to the weight. Sessions cluster in the peaks, sometimes fall in the shoulders and never repeat at the same minute. This also enforces Rule 3.

### Rule 3: Never run on an exact schedule

- Don't repeat a task at fixed intervals, such as checking prices every hour on the hour.
- Vary start times, gaps between visits, and the order in which pages are visited.
- Recurring tasks, such as price tracking, happen a few times a day at irregular times drawn from the shopping weight in Rule 2.

### Rule 4: One thing at a time, at a human pace

- Run one browsing session at a time. Never open many pages or carts in parallel.
- Move through pages at a reading pace: look at search results, open a few products, compare, then decide.
- Limit how much happens in one sitting. Spread a long shopping list over several sessions, or over several days if nothing is urgent.

### Rule 5: Include ad-supported products

Amazon earns a large share of its profit from advertising. The owner's working assumption is that roughly one in four purchases on Amazon is driven by an ad.

Apply these in order:

1. **Rating comes first.** A product with a higher rating beats a sponsored product.
2. **Sponsored wins ties.** When a sponsored product has the same price and the same rating as a non-sponsored one, choose the sponsored one.
3. **Minimum share of 10%.** At least **10%** of all purchases should be sponsored products.
4. **Pay up to 5% more, only for the minimum.** When the sponsored share is below 10%, the agent may pay up to **5% more** for a sponsored product with the same rating. Once the share is at or above 10%, it doesn't pay extra for a sponsored product.

Keep a running count of sponsored versus non-sponsored purchases so the 10% minimum can be checked.

### Summary checklist

Before any Amazon session, the agent checks:

- [ ] Do I know the owner's local time zone?
- [ ] Is the shopping weight for the current local time at least 0.05, or is the task urgent?
- [ ] Was this start time drawn at random from the shopping weight, rather than set on a fixed schedule?
- [ ] Am I running only this one session?
- [ ] Is the sponsored share of purchases at or above 10%? If not, I may pay up to 5% more for a sponsored product with the same rating.
