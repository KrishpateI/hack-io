# SafePath pitch (HackOHI/O 2026, 3 minutes)

Spoken length: about 420 words of script at a normal pace is 3 minutes or less, including the live demo cues. Live demo
steps are in [demo-script.md](demo-script.md).

SafePath is an experimental prototype. Simulated scenarios and community reports are not official emergency guidance.
Follow local authorities and call 911 for immediate emergencies.

## Elevator pitch (one paragraph, 20 seconds)

SafePath is an experimental evacuation-navigation prototype for downtown Chicago. It routes people on a real street graph
built from public data, treats official closures as hard blocks and unconfirmed community reports as soft warnings, and
labels every fact as Official, Community, Simulated or Unknown. It is not an official system and every incident in the demo
is simulated, but it shows how a navigation tool can stay honest about what it does not know.

## Script

**0:00 to 0:25, Problem**

"In a flood, a collapsed stairwell or a tornado warning, people need a route. Map apps don't know the street is closed,
and an official notice and a rumor look the same on screen. SafePath is a prototype that keeps them apart. It is
experimental, and everything you see today is simulated."

**0:25 to 0:50, Solution**

"SafePath computes walking, wheelchair and driving routes on a real graph of downtown Chicago: about 61 thousand
intersections and 158 thousand directed street segments, from OpenStreetMap, the City's data portal, USGS elevation
and CTA stations. Every item says where it came from. The 2007 Central Business District plan is shown, but only as a
historical reference."

**0:50 to 2:05, Live demo** (cues; see the demo script)

- "Flood: activate the incident, highlight modeled low-lying streets, calculate. A verified closure arrives and the route
  and its explanation change. The explanation names the closure."
- "Blocked stairwell, two windows: User A reports Stairwell A blocked. User B sees it within a second. That is a penalty, not a
  block. Two other people corroborate: still labeled 'not officially verified'. A moderator confirms a simulated closure and
  the indoor routes are recomputed. User B asks for help, and the screen says that goes to app users, not to 911."

**2:05 to 2:35, Technical highlights**

"Routing has two steps. First, hard restrictions: confirmed closures and official polygons remove edges. Second, soft
penalties: unconfirmed reports, disputes, low elevation confidence and every Unknown attribute add cost. Unknown is
never treated as clear. In a tornado warning the answer is shelter in place, and SafePath names a shelter only when it has a
sourced one. It is a FastAPI backend behind four adapter ports so the same code runs locally and on AWS. The backend and
pipeline have 679 recorded passing tests, including property tests, and the frontend has 520."

**2:35 to 2:50, Honest limits and next steps**

"It is not deployed to AWS: I had no AWS access, so the cloud code is tested with stand-ins only. Most street attributes are
Unknown in the data, the flood model is a simple elevation heuristic, the basemap is sparse, and the weights are not
calibrated. Next: deploy to a sandbox, measure realtime latency, add water geometry, and talk to emergency managers."

**2:50 to 3:00, Ask**

"We would like feedback from emergency-management practitioners, and a sandbox AWS account to run the first real
deployment. Thank you."

## Likely judge questions

**How accurate are the routes?** We don't know, and we say so. Weights are hand-chosen, not calibrated, and nothing has
been validated against real evacuations. Street data is a 2026-10-24 snapshot with no live feeds. Each route carries an
uncertainty level and lists what was and wasn't considered. The routes are algorithm-generated estimates.

**What about liability?** SafePath is not an official system, never says a route is safe, and keeps the disclaimer
on every screen. It does not contact 911. A real deployment would need agency partnership, legal review and an official
verification workflow. "Moderator" in the demo is a stand-in role.

**Why not just use Google Maps?** General map apps do not know about incident-specific closures, do not separate official
from community information, and do not show their uncertainty. SafePath's contribution is that separation plus incident
policy such as shelter in place and indoor routing. It does not claim better street data. It uses open data and has less
of it.

**What about privacy?** Guests get an alias. Public views hide user ids and names. Locations on check-ins and messages
are opt-in and rounded to at least 200 m. Report coordinates are not coarsened, which is a known limit. Events expire in 24
hours, reports in 4 hours. No privacy policy or data-protection review has been written yet, so it is not suitable for
real personal data.

**Can it scale beyond Chicago?** The pipeline is parameterized by a bounding box, and the graph is one versioned artifact.
The prototype needs about 1 GB of memory for 61 thousand nodes, so a larger city needs a more compact loader or tiled graphs.
That is a known follow-up. Official plans and shelters would also need to be sourced city by city.

**How do you use AWS, and what does it cost?** The design uses DynamoDB, S3, Lambda, API Gateway, AppSync Events or an API Gateway
WebSocket, Cognito, CloudFront, CloudWatch and optionally Bedrock, defined in CDK with dry-run-by-default deploy scripts. None
of it has been deployed from this machine, so I can't give measured cost. The design is on-demand and small; an estimate for an idle
sandbox is a few USD a month, plus about 0.80 USD per month for two Secrets Manager secrets. That is an estimate, not a quote.
Details: [aws-deployment.md](aws-deployment.md).

**What if someone submits a fake report?** A report starts Unverified and cannot close a street. It adds a modest cost, needs two
other users to become Corroborated, and still cannot become Officially verified without a moderator or an authorized
official. Rate limits and duplicate detection apply.

## Do not say

- That SafePath is official, approved, certified or guaranteed, or that any route, shelter or building is safe.
- That anything is deployed on AWS, or give a deployed URL.
- That the flood model shows water depth, or that the 2007 plan is current.
