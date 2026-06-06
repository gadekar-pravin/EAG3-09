
Session 9: Browser Agents & Autonomous Web
Session 9 adds one skill to the agent: Browser. The skill fetches web pages and interacts with them. It picks the cheapest way to do the job and only spends more when the cheap way fails. Gateway V9 also gets an upgrade so it can send images to vision models when the most expensive layer is needed.

This file explains what was built, why it works, what broke during integration, and what students need to do for the assignment.

1. Where the course stands
Session 6 built one agent with four roles (Perception, Memory, Decision, Action) that ran in a loop. Session 7 added semantic memory through FAISS (a vector search library from Meta), Semantic Chunking pending. Session 8 replaced the loop with a graph using NetworkX (a Python graph library) and added a skills catalogue so a planner could compose specialised agents into a multi-agent DAG.

After Session 8, the runtime is general and the skill abstraction works. The one thing it still cannot do well is browse the live web.

Session 8's Researcher skill uses two MCP tools: web_search and fetch_url. Both work fine on clean static HTML. fetch_url returns a rough text version of the page, which is enough for blog posts and documentation. It fails on JavaScript-rendered pages, on widgets that only show their content after a click, on multi-page workflows, and on any site that hides results behind search and filter steps.

Session 9 closes that gap with one new skill.

2. What Session 9 adds
S9 ships three things:

A Browser skill that goes into agent_config.yaml and gets called by the planner like any other skill.
Gateway V9 with a new POST /v1/vision endpoint that accepts an image plus a prompt and routes the call to a vision-capable model.
Teaching notes with five worked examples, three diagnostics, and a cost ledger that shows real numbers.
S9 does NOT add: real authentication, browser-profile persistence, anti-detection beyond the polite default, or control of desktop applications. The Browser skill operates inside a normal browser tab on publicly reachable pages. The assignment extends the runtime into the deferred areas.

The S8 orchestrator (flow.py) did not change. Adding the Browser skill needed only a yaml entry, a prompt file, and a small dispatch branch in skills.py. This is the S8 promise honoured: a new skill plugs in without runtime edits.

3. The four-layer cascade
A 2026 browser agent that costs cents per session does the cheap thing first and escalates only when the cheap thing fails. The Browser skill has four layers.

nothing useful extracted

selectors break or items missing

tree empty or no clear action

page never renders

Layer 1: extract
httpx + trafilatura
0 LLM cost

Layer 2a: deterministic
Playwright + stable selectors
0 LLM cost

Layer 2b: a11y
Playwright + accessibility tree
cheap text LLM judgment

Layer 3: vision
Playwright + set-of-marks + VLM
per-call vision cost

Precondition: gateway access
CAPTCHA / login / geo / rate limit

error_code: gateway_blocked

Layer 1: extract. Use httpx (Python HTTP library) to download the page, then trafilatura (a library that pulls main article text out of messy HTML) to get the content. No browser is launched. No LLM is called. This handles all static content sites.

Layer 2a: deterministic. Use Playwright (a tool that controls a real browser from Python code) with hand-written CSS selectors. CSS selectors are rules like .product-title that find elements on a page. When selectors are stable and known for a target site, the agent clicks and extracts directly. No LLM is called here either.

Layer 2b: accessibility tree. Use Playwright plus the accessibility tree, called the a11y tree. The a11y tree is the structured view of a page that screen readers use. It contains only meaningful elements (buttons, links, form fields, headings) and skips decoration, scripts, and hidden markup. A cheap text LLM reads a short text summary of the tree and picks an action. Playwright runs the action.

Layer 3: vision. Use Playwright plus set-of-marks plus a VLM. A VLM (vision-language model) is a model that can read an image and answer questions about it. Set-of-marks means drawing numbered boxes over the clickable elements in a screenshot so the model can point at things by number. This is the most expensive layer and is only used when the a11y tree is empty or unhelpful.

Precondition. Above all four layers sits a check: the page must actually load. When CAPTCHA (the "are you a human" challenge), login walls, geographic blocks, or rate limits stop the page from rendering, no later layer can save the agent. The Browser skill detects the common blocking patterns and returns error_code="gateway_blocked" so the orchestrator can try another path.

4. The accessibility tree, in more detail
Every browser keeps a parallel data structure of the page called the accessibility tree. The operating system uses it to talk to screen readers. It strips out CSS, scripts, hidden elements, and visual fluff. It keeps only buttons, links, form fields, headings, and the ARIA labels developers added so screen readers know what each element is.

The size difference is dramatic. A large commercial news home page is around 6 MB of HTML and 30,000 nodes. The same page's a11y tree is around 30 KB and a few hundred nodes. That is a 200x reduction with no loss of useful information for an agent that wants to know what is on the page and what can be clicked.

For the Hugging Face test in state/sessions/s9_hf_top3, the page DOM (the full page structure JavaScript can see) was around 1.1 MB. The a11y tree was 30 KB. After a dedupe pass that removed nested decorations counted as separate items, there were 230 real interactive elements.

Playwright exposes the a11y tree in two ways: page.accessibility.snapshot() for a compact summary, and the lower-level Chrome DevTools Protocol (CDP, the wire format DevTools uses to talk to Chrome) for the full tree when debugging.

5. Set-of-marks, in more detail
When the a11y tree is empty or unhelpful, the Browser skill takes a screenshot and draws numbered boxes over the clickable elements. The VLM sees the screenshot with numbers like [1] [2] [3] and picks one. The skill then translates the picked number into a click.

Three details matter and would be easy to get wrong.

The boxes are drawn after the screenshot, with Pillow. Pillow is a Python image library. The other option, injecting boxes as JavaScript overlays into the live page, changes the page's own rendering and breaks click events. Drawing on the screenshot after capture keeps the page clean.

The drawing has to account for device pixel ratio (DPR). DPR is the ratio between CSS pixels (what the layout engine works with) and actual screen pixels (what the camera captures). On a high-resolution display, a button at CSS coordinate (200, 300) is at screen coordinate (400, 600). If the box is drawn at the CSS coordinate, it lands somewhere else on the screenshot. The highlight module reads the page's DPR and scales the box coordinates.

The list of clickable elements has to be deduped. The first Excalidraw test failed because a small SVG <rect> decoration inside the rectangle tool button was counted as a separate clickable. The model saw two boxes around the same button, picked the inner one, and clicked the decoration. After deduping, the toolbar went from 30 noisy boxes to 10 clean tool buttons. The next test ran in 1 turn, 2.4 seconds, using 1,764 input and 155 output tokens.

The dedupe story is the diagnostic discipline from Session 7 again. The model did the right thing given what it was shown. The boxes were wrong. When the agent picks something strange, look at what the agent could actually see.

6. Layer 1 example: Hacker News
Layer 1 is the simplest possible path:

import httpx, trafilatura
html = httpx.get(url, timeout=10).text
content = trafilatura.extract(html, output_format="markdown")
Against Hacker News, this turns 35 KB of HTML into 9.4 KB of structured story listings. Sub-second. Zero LLM cost.

The skill wraps this with a check for CAPTCHA pages (so the agent doesn't waste tokens on a block page) and a check that the extracted content is actually useful (at least 200 characters and contains at least one keyword from the goal). When the check passes, the cascade returns immediately. When it fails, Layer 2 takes over.

output.path = "extract". Wall-clock 2.1 seconds. Zero entries in the V9 cost ledger for the Browser.

7. Layer 2a example: Amazon
When the page has reliable CSS selectors, Playwright clicks and extracts without an LLM. The Amazon product page test shows the pattern.

The goal is to find the top organic search result for a query, go to the product page, and extract price, title, brand, and description. The selectors are hand-written and pinned to the current Amazon DOM.

A vanilla headless browser (a real browser running with no visible window) gets blocked by Amazon's anti-bot detection and sees a CAPTCHA. Three small changes get past the polite-default detection without crossing any ethical lines:

context = await browser.new_context(
    user_agent="Mozilla/5.0 (Macintosh; ...) Chrome/124.0.0.0 Safari/537.36",
    java_script_enabled=True,
)
await context.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined})
""")
A real Chrome user-agent string, the --disable-blink-features=AutomationControlled flag, and removing the navigator.webdriver marker that headless Chrome sets by default. No residential proxy. No visible browser window. The agent still honestly identifies as a Python process; it just looks less obviously like a script.

Result: 2.7 MB of HTML becomes 2.2 KB of clean JSON. 0.1% of the original size. Zero LLM calls. Wall-clock 4.3 seconds, mostly page load.

Most teams skip Layer 2a because writing selectors is boring. It is also the layer that pays for everything above it. When 80% of the targets a production agent visits yield to a hand-written selector, the other 20% become affordable.

8. Layer 2b example: Hugging Face filter
When selectors break, the agent switches to the a11y tree. The Hugging Face test is the canonical example.

The goal: go to huggingface.co/models, filter by text-generation, filter by transformers, sort by most likes, and read the top three model cards.

The counterfactual was run first. Five categories of hand-written selectors were tested. Four worked (the search bar, the filter toggles, the sort menu trigger, the model card containers). One failed completely: the popover options. A popover is a small panel that opens when a trigger is clicked. The options inside it do not exist in the DOM until the trigger is clicked. Static selectors cannot grip an element that does not yet exist.

The a11y tree solves this. The skill reads a fresh a11y summary at the start of each turn. After the trigger is clicked, the next turn's summary includes the popover options.

The loop is:

turn 1: read a11y summary, send to cheap LLM, get action, run it
turn 2: read a11y summary (now includes popover options), repeat
...
Result: 5 turns, 5.6 seconds, 9,620 input tokens, 408 output tokens. Cost from the V9 ledger: $0.00 on the free tier. Final URL: huggingface.co/models?pipeline_tag=text-generation&library=transformers&sort=likes.

8.1 The dropdown-as-fence diagnostic
The first run took 3 turns and failed. The model emitted two actions in one turn: click the sort trigger, then immediately click one of the sort options. The second click missed because the popover had not yet rendered when the action was queued. The agent reported success and the page state was wrong.

The fix is small. The lesson is general. Layer 2b has no eyes between turns. The agent reads the a11y summary at turn start and acts blindly until turn end. When an action changes the page (a click that opens a popover, a navigation that loads a new view), any later action in the same turn is operating on stale information.

The rule encoded in browser/driver.py:

Max 2 actions per turn overall.
Items whose name ends with ▾ or : or starts with Sort: are dropdown triggers.
When the chosen action targets a dropdown trigger, it must be the only action of the turn.
The next turn re-reads the summary, sees the popover options, and picks one with current information. Five-turn pass, $0.00 cost.

The lesson works beyond dropdowns. Any DOM change between an action and its follow-up needs a fence. Dropdown opens, form submits, modals show, routes load. All of them.

9. Layer 3 example: a canvas-only page
Layer 3 is the most expensive layer. The S9 implementation against six real canvas-heavy targets escalated to Layer 3 exactly once, and only against a page built specifically to defeat the a11y path.

The six real targets and where the cascade actually landed:

Target	Goal	Path chosen	Why
tldraw.com	draw an ellipse in a quadrant	a11y	toolbar has ARIA labels, drag action handles canvas
photopea.com	open canvas, draw red rectangle	a11y	menus and palette have ARIA labels
piskel-app	fill a region with colour	a11y	tool buttons labelled, click coords work for pixels
openprocessing.org	interact with a sketch	a11y	controls labelled at sketch level
chromedino offline	start the game	a11y	start button has an accessible name
polymer-shadow-demo	open a shadow-DOM widget	a11y	shadow boundary surfaced through composed events
The seventh target was a single HTML file served locally with one <canvas> element, three coloured shapes drawn on it, and nothing else. The goal: click inside the red circle. The page exposed no a11y nodes beyond the document root because canvas pixels are not DOM elements. The a11y summary was empty.

The cascade tried extract (no content), Layer 2a (no selectors), Layer 2b (6 turns of the cheap LLM returning done(success=false) because the summary was empty), then escalated to Layer 3. The vision call found the red circle by colour, returned a click coordinate, and Playwright dispatched it. One vision turn. Total wall-clock 29.7 seconds. Total cost $0.00 on free-tier Gemini Flash-Lite. Seven Gemini calls overall, 4,867 input tokens, 620 output tokens.

Two conditions had to hold for the natural Layer 3 escalation:

The page had no clickable elements the a11y tree could list, so Layer 2b had nothing to act on.
The goal explicitly said "do not guess," so Layer 2b could not declare success on an empty summary.
This is the main finding of Session 9. Modern canvas applications ship ARIA-labelled toolbars around their canvas, and the Browser driver's drag action handles raw canvas drawing without seeing the page. Layer 2b is wider than the field assumed when set-of-marks was introduced in 2024. Layer 3 is a narrower, real, and rare part of the cascade.

A browser agent in 2026 that fires the visual path on every page has skipped a layer that would have cost cents per session. The cost cascade is the architecture.

10. The precondition layer: Redfin
The cascade has an implicit step above Layer 1: the page must load. When CAPTCHA, login walls, or rate limits stop the page, no layer can save the agent.

The Redfin test surfaced this. The goal: extract bed count, bath count, and price from a Redfin listing URL. The httpx request returned a "Let's confirm you are human" page. The counterfactual was run for evidence: 0 of 16 hand-written selectors matched anything resembling property data. Layer 2b was tried: the a11y tree contained only the CAPTCHA's "Begin" button and a language selector.

The skill's detect_gateway_block recognised the pattern, returned AgentResult with error_code="gateway_blocked", and emitted no content. The Planner was re-invoked with the failure report and produced a recovery sub-DAG that searched for the same listing through a different source. The Formatter received the recovered content and gave an honest final answer.

The precondition layer is named in the cascade diagram but not solved by the shipped skill. Solving it means real authentication, session and cookie storage, residential proxies, and the ethical questions that come with each. Those decisions belong to whoever is running the agent. The assignment in §17 invites students to make them deliberately for at least one target.

11. How the skill plugs into the runtime
Planner

Researcher
find candidate URLs

Browser
open, filter, click, verify

Distiller
extract structured fields

Replay Viewer
show evidence

Formatter
final answer

The Browser skill is one entry in agent_config.yaml:

browser:
  prompt: prompts/browser.md
  description: |
    Fetches and interacts with web pages through a four-layer cascade
    (extract, deterministic, a11y, vision). Input metadata accepts url
    (required) and goal (required). Returns BrowserOutput with the
    chosen layer surfaced as output.path. Use when the Researcher
    skill's fetch_url is insufficient: JavaScript-rendered content,
    interactive widgets, multi-page workflows.
  provider_pin: null
The skill itself is S9/code/browser/skill.py, around 280 lines. It owns the cascade decisions and calls the layer-specific drivers under browser/: client.py for gateway calls, dom.py for clickable-element enumeration and block detection, highlight.py for set-of-marks drawing, driver.py for the interaction loop shared between Layer 2b and Layer 3.

The Hugging Face multi-agent run produces this DAG:

Planner

Browser
path: a11y
5 turns / $0.00

Distiller

Formatter

The Planner produces the four-node graph. The Browser node walks the cascade and lands on path = "a11y". The Distiller extracts the model name, parameter count, and description from each card. The Formatter renders the final answer. The replay.py viewer steps through the four nodes and shows the Browser's chosen layer without any orchestrator awareness of what a layer is.

The Pydantic model in schemas.py:

class BrowserOutput(BaseModel):
    url: str
    goal: str
    path: Literal["extract", "deterministic", "a11y", "vision"]
    turns: int
    content: str | None = None
    actions: list[dict] = []
    final_url: str | None = None
AgentResult gains an error_code field with five values: gateway_blocked, extraction_failed, interaction_failed, timeout, vlm_unavailable. The Planner reads error_code separately from output and produces different recovery shapes for each.

12. Four integration diagnostics
Four bugs surfaced during integration and validation. All four returned valid responses while doing the wrong thing.

1. The V9 client default URL was wrong. The Browser skill's client was correctly pointed at port 8109 (V9). Other skills inherited a default URL that still pointed at port 8108 (V8). Result: non-Browser LLM calls landed on V8 and the V9 cost ledger never saw them. The calls succeeded. No error fired. The visible behaviour was correct.

Detection only happened because the V9 ledger was inspected directly and showed Browser calls but no Planner, Distiller, or Formatter calls. Lesson: when a versioned service is introduced, audit the default URL every place a client is built. Silent miswires of this shape leave no error trace because the wrong server returns valid responses.

2. SQLite stopped writing across a day rollover. The V9 cost ledger writes to SQLite (a file-based database). At UTC midnight, writes silently stopped. The skill returned valid responses. The ledger showed flat lines after midnight. A process restart fixed it. The connection object was retained from startup and the day-keyed write path hit a stale schema reference.

Lesson: long-lived database connections that depend on time-keyed paths need to refresh on the boundary. Same shape as bug 1: the success path and the failure path look identical from the calling code.

3. Recovery amnesia. The most useful one. The S8 orchestrator handles a failed node by adding a fresh Planner node carrying the failure report. There is no per-node retry counter. Recovery means re-planning.

The recovery Planner was given inputs=["USER_QUERY"] and the failure report. It saw nothing else. It did not know that siblings n:2 and n:3 had already finished, because the orchestrator never showed them. The planner's failure rule said only "if FAILURE appears in the prompt, do not re-emit the failing step on the same inputs." With no view of sibling state, the safe behaviour was to re-plan the whole sub-DAG. The orchestrator ran the fresh sub-DAG. The agent silently re-ran work that was already done.

This is a different shape of silent failure from bugs 1 and 2. Those returned wrong data. This returned correct data through wasted work. The signal is on the cost ledger: when the same query roughly doubles its token total after a single mid-run failure, the recovery Planner is re-running siblings it should be wiring.

The fix lives in S9/code/flow.py:277-304. The recovery dispatch now collects prior_complete, the ids of all completed non-planner non-critic nodes, and wires them into the recovery Planner's inputs alongside USER_QUERY. S9/code/prompts/planner.md gains a recovery section: when FAILURE is in the prompt and inputs include n:* ids, those ids are siblings that already succeeded. Wire them by id. Only re-emit the failing branch. The log line now prints ↪ recovery (upstream_failure): planner node n:X queued for n:Y; reusing 2 prior result(s): n:2, n:3 so the carry is visible.

Three deterministic tests in S9/code/tests/test_recovery_amnesia.py cover the carry behaviour, the exclusion of critics and planners, and the legacy fallback when no priors exist. 25 of 25 tests pass.

Lesson: any agent invoked during failure recovery has to receive the partial state of the work in progress, passed in its inputs and named in its prompt. The orchestrator has the state. Handing it to the recovery agent is the orchestrator's job.

12.2 Critic auto-insertion on pre-planned graphs
The fourth diagnostic has two parts. Fixing the first exposed the second.

Part 1: the critic that never fired. The diagnostic came from a student running the Claude Shannon query. The Distiller in agent_config.yaml carries critic: true. The promise is that whenever a Distiller node feeds another node, a Critic gets spliced onto the edge to check the Distiller's output for hallucinated fields. The student reported that the safety net never fired. No Critic ever entered the graph.

The bug was a four-character short-circuit in Graph.extend_from:

if src_def.critic and added:
    # splice a Critic between src and each newly-added child
added is the list of children dynamically spawned by the completing node. When the Planner emitted the full pipeline up front (Planner, Researcher, Distiller, Formatter all declared in the first plan), the Distiller did not spawn anything dynamically. Its successor was already in the graph. added was the empty list. The auto-insert silently bypassed. The critic: true flag became a no-op in the common case.

Same shape as the recovery amnesia bug. The pipeline ran. The answer came back. The promise the yaml made was quietly broken.

The fix at S9/code/flow.py:153-167 drops the short-circuit. Instead of looking at newly-spawned children only, the code reads the completing node's actual outgoing edges. For each non-Critic child (whether dynamically added or pre-planned), it splices a Critic node onto the edge: re-routes src → child to src → critic → child and stamps metadata.target and metadata.child so the existing critic-fail recovery path and the per-target recovery cap keep working.

Part 2: the critic that fired on the wrong question. Once the Critic did enter the graph, the smoke test produced a different failure. Eight consecutive critic-fail recoveries on a city-population query. Each Critic rejected the Distiller's output as off-topic. The output was correct. The Critic was confused.

The Critic's inputs were the upstream skill's output only. With no USER_QUERY in scope, the prompt-render path fell back to the session's MEMORY HITS for context. MEMORY HITS at that moment included queries from prior sessions, one of which was the Hugging Face model search. The Critic read those hits, decided the user was asking about Hugging Face models, and rejected the city-population output as unrelated. Eight times in a row, until the global node cap stopped the run.

Same shape again. The Critic behaved correctly given what it could see. What it could see was wrong.

The fix at S9/code/flow.py:172-176 is one line. The auto-inserted Critic now receives inputs=["USER_QUERY", src_nid]. The Critic sees both the original ask and the upstream output. The MEMORY HITS fallback is no longer load-bearing for Critic context.

Four deterministic tests in S9/code/tests/test_critic_autoinsert.py cover: a pre-planned Distiller-to-Formatter edge gets a Critic, an explicit Planner-emitted Critic is not duplicated, multiple outgoing edges each get their own Critic, non-critic skills do not trigger insertion, and the auto-inserted Critic receives USER_QUERY in its inputs. The full suite is 29 of 29 passing (22 original recovery tests, 3 recovery-amnesia tests, 4 critic tests).

Lesson, two layers deep. First layer: orchestrator invariants belong in the orchestrator. The critic: true flag is a runtime promise the orchestrator keeps regardless of what the Planner emits. Second layer: an evaluator agent needs to see both what it is evaluating and the original framing of what was asked. A Critic with only the output is a Critic guessing at the question.

Known follow-up, deferred. The critic-fail recovery cap (recovered_branches[target_nid]) is keyed by target node id. It does not key on the target skill. Each new recovery cycle creates a new Distiller node with a new id, so the cap never fires when the same skill keeps failing across cycles. With Part 2 fixed this is much less likely to bite, but a future Distiller that genuinely keeps producing bad fields will only be stopped by the global 60-node cap. A recovered_skills cap is the belt-and-braces patch when the failure mode shows up in production.

All four bugs share the same shape as the _format_hits bug from Session 7. The success path and the failure path look identical from the caller's view. Detection means inspecting the side effect: ledger contents on disk, token totals on the per-query log, files written or not written, Critic nodes present or absent in the executed graph, what context the evaluator agent actually saw at the moment of its verdict.

13. Two gateway fixes worth marking
Both fixes live in llm_gatewayV9/providers.py. The Browser skill did not change. The rule from earlier sessions held: gateway owns provider quirks, callers stay clean.

GitHub's json_object keyword refusal. When V9 fails over from a primary vision provider to GitHub-hosted models with a structured output request, the request format has to downgrade from response_format=json_schema to response_format=json_object. GitHub's API rejects json_object requests whose system message does not contain the literal word "json". V9 now injects the word into the retried system message. Without this, every Gemini-to-GitHub vision failover returned a 400 error.

Routing race between Gemini and GitHub. The router was marking Gemini as cooling (after a rate-limit response) and immediately trying GitHub before GitHub's circuit had closed. Combined with the first fix, the vision failover chain now works end-to-end.

When the same caller code runs against V3, V7, V8, and V9 without changes, the gateway is doing its job.

14. Cost-discipline numbers
The cascade's claim is empirical. The runs are in state/sessions/.

Layer	Target	Path chosen	Turns	LLM cost	Wall-clock
1	news.ycombinator.com	extract	0	$0.00	2.1s
2a	amazon.com product page	deterministic	0	$0.00	4.3s
2b	huggingface.co/models	a11y	5	$0.00	5.6s
3	local canvas-only.html	vision	7	$0.00	29.7s
precondition	redfin.com listing	gateway_blocked	0	$0.00	1.4s
Every run came in at $0.00 on the free-tier Gemini 3.1 Flash-Lite. The claim that a browser agent built on the cascade costs cents per session is the upper bound. The observed lower bound is fractions of a cent.

The 29.7 seconds at Layer 3 is the cascade doing its work. Six turns of Layer 2b tried the empty page and returned done(success=false). Layer 3 then took one vision turn. The wall-clock is the cost of trying the cheap path first. The dollar cost is unchanged.

15. Design choices worth flagging
The shipped Browser skill uses Playwright, Pillow, httpx, trafilatura, and the V9 gateway client. No agentic frameworks.

The force_path knob exists for two reasons: debugging (to exercise a specific layer during testing) and the rare production case where the caller already knows from context that vision is required (for example, a downstream skill that produced a screenshot artifact and wants the Browser to act on it). The natural cascade is the default. The knob is opt-in metadata.

The driver code under S9/code/browser/ was ported from the experimental phase unchanged. The shared BaseDriver structure, with SetOfMarksDriver and A11yDriver differing only in _decide(), held up under integration.

The detect_gateway_block helper sits in browser/skill.py instead of browser/dom.py. The integration pass was told to leave the driver core untouched. This is documented as a small spec deviation in VALIDATION.md §7. A future pass will move it to dom.py where it structurally belongs.

. Closing
The main finding from §9 is worth repeating. Across six real canvas-heavy targets that everyone would have predicted needed vision, the natural cascade landed on a11y every time. The visual path was only needed against a target built specifically to defeat the a11y path. Modern accessibility coverage is wider than the field assumed when visual web agents were introduced. The drag-by-coordinate action handles raw canvas drawing without seeing the page. Vision is really only used when nothing else works.

A browser agent built in 2026 that fires the visual path on every interaction has skipped layers that would have cost a fraction of the budget. The cascade is the architecture. The replay trace makes it visible. The cost ledger makes it measurable. The skill catalogue makes it composable into the multi-agent runtime from Session 8.

Session 10 takes the cascade off the browser tab and onto the desktop.

17. Glossary: terms and libraries used in this session
This section explains every term and library that appears in this file. Use it as a reference. Official documentation links are included where the project has one.

Python libraries
httpx. A modern Python library for making HTTP requests. Similar to the older requests library but supports async and HTTP/2. The Browser skill uses it in Layer 1 to download pages without launching a browser. Docs: https://www.python-httpx.org/

trafilatura. A Python library that takes messy HTML from a real web page and pulls out the main article text. It removes navigation, ads, footers, and other clutter. The Browser skill uses it in Layer 1 right after httpx downloads the page. Docs: https://trafilatura.readthedocs.io/

Playwright. A tool from Microsoft for controlling a real Chromium, Firefox, or WebKit browser from code. You write Python (or JavaScript or other languages), Playwright clicks buttons and types text in an actual browser. The Browser skill uses Playwright for Layers 2a, 2b, and 3. Docs: https://playwright.dev/python/

Pillow. The standard Python library for working with images. You can open, edit, draw on, and save images. The Browser skill uses Pillow to draw numbered boxes on screenshots for set-of-marks. Docs: https://pillow.readthedocs.io/

FAISS. A vector search library from Meta. Given a query vector, it finds the closest matching vectors in a stored collection. Session 7 added FAISS for semantic memory. Repo: https://github.com/facebookresearch/faiss

NetworkX. A Python library for graphs (nodes connected by edges). Session 8 uses it as the substrate for the multi-agent DAG. Docs: https://networkx.org/

Pydantic. A Python library that validates data against typed models. Every typed boundary between agents in the course uses Pydantic. AgentResult, BrowserOutput, and NodeSpec are all Pydantic models. Docs: https://docs.pydantic.dev/

SQLite. A small, file-based database that comes built into Python. The V9 cost ledger writes to a SQLite file. No server is needed. Docs: https://www.sqlite.org/

Protocols and standards
MCP (Model Context Protocol). An open protocol for an AI model to call external tools. Session 4 covered MCP in depth. The course uses MCP for web_search, fetch_url, search_knowledge, and other tools. Docs: https://modelcontextprotocol.io/

CDP (Chrome DevTools Protocol). The wire format that Chrome DevTools uses to talk to the Chrome browser. It is the same protocol Playwright uses under the hood to control Chromium. The Browser skill uses the lower-level CDP only for debugging the full accessibility tree. Docs: https://chromedevtools.github.io/devtools-protocol/

ARIA (Accessible Rich Internet Applications). A web standard for accessibility labels. Web developers add ARIA attributes like role="button" or aria-label="Sort by likes" so screen readers know what each element is. The accessibility tree is built from ARIA plus the underlying HTML semantics. Docs: https://www.w3.org/WAI/standards-guidelines/aria/

DOM (Document Object Model). The structured representation of a web page that JavaScript can read and modify. When you open DevTools and look at the Elements tab, you are looking at the DOM. Pages can be 6 MB of DOM and still only 30 KB of accessibility tree. Docs: https://developer.mozilla.org/en-US/docs/Web/API/Document_Object_Model

Concepts
Accessibility tree (a11y tree). A parallel structured view of the page that browsers maintain for screen readers. It contains only meaningful elements: buttons, links, headings, form fields, landmarks. It strips out CSS, scripts, hidden elements, and decoration. "a11y" is shorthand for "accessibility" (a, then 11 letters, then y).

Set-of-marks. A technique for letting a vision-language model pick an element on a page. Take a screenshot, draw numbered boxes over each clickable element, and ask the model to pick a number. The convention emerged around 2024 and is the standard input format for visual web agents. Background paper: https://arxiv.org/abs/2310.11441

VLM (Vision-Language Model). A model that can read both text and images. Examples in 2026: Gemini 3.1 Pro, GPT-5.5, Claude Opus 4.7, Qwen2.5-VL. The Browser skill uses Gemini 3.1 Flash-Lite by default through V9.

LLM (Large Language Model). A model that reads and writes text. All the planners, distillers, and formatters in this course are LLMs. A VLM is an LLM that can also read images.

DPR (Device Pixel Ratio). On high-resolution displays (like Retina), one CSS pixel maps to more than one screen pixel. A button at CSS coordinate (200, 300) is at screen coordinate (400, 600) on a 2x display. Set-of-marks has to account for this so the boxes land on the right elements in the screenshot.

Headless browser. A real browser (Chromium, Firefox, WebKit) running without a visible window. The page loads, JavaScript executes, the DOM is built. The only thing missing is the window on your screen. Headless is faster and uses less memory than headed browsing.

CSS selectors. Rules that pick elements on a page. Examples: .product-title picks anything with the CSS class product-title. #main-button picks the element with id main-button. div.card > h2 picks an h2 directly inside a div of class card. Layer 2a uses CSS selectors hand-written by the developer. Reference: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_selectors

CAPTCHA. A challenge that asks "are you a human?" Common forms: pick the squares with traffic lights, type the wavy text, hold the button. CAPTCHAs block agents at the precondition layer.

Popover, dropdown. Small UI panels that appear when a trigger is clicked. A sort dropdown that opens to show "Most likes / Most downloads / Newest" is a popover. The items inside do not exist in the DOM until the trigger is clicked.

DAG (Directed Acyclic Graph). A graph of nodes connected by edges where the edges have a direction and there are no cycles. Session 8 uses a DAG to represent the plan: nodes are agents, edges are dependencies, execution flows in the direction of the edges.

Sites and applications mentioned
Hugging Face. A platform for sharing open-source models and datasets. The Layer 2b test filters the model index by tag and sorts by popularity. Site: https://huggingface.co/

Excalidraw. A browser-based whiteboard drawing tool. The set-of-marks dedupe diagnostic was found here. Site: https://excalidraw.com/

tldraw, Photopea, Piskel, OpenProcessing. Browser-based drawing, image editing, pixel art, and sketch tools tested in §9 for the natural Layer 3 cascade. All escalated to Layer 2b instead.

Redfin. A real-estate listing site. The Layer 1 request returns a CAPTCHA page, which surfaced the precondition layer in §10.

Frameworks the course does NOT use
LangChain, LlamaIndex, CrewAI, AutoGen. Third-party agentic frameworks. The course does not use any of them on the shipped path. The reason: students should be able to write the gateway, the skill catalogue, and the orchestrator themselves so they understand how each piece works. The assignment forbids these frameworks for the same reason.

18. Assignment: Browser Comparison Agent + Replay Viewer
Build a browser-capable agent that completes a real comparison task on the web and produces a replay view of the run.

The goal is to demonstrate work that Session 8’s web_search + fetch_url cannot reliably do: interacting with dynamic pages, filters, dropdowns, tabs, search forms, product cards, pricing pages, or multi-step workflows. web_search and fetch_url are useful for static pages, but they fail on JavaScript-rendered pages, click-revealed widgets, multi-page flows, and sites where useful data appears only after filtering or sorting.

Students must choose one comparison task, such as:

Compare 3 laptops under ₹80,000.
Compare 5 AI coding tools by free plan and paid plan.
Compare top 3 Hugging Face text-generation models sorted by likes.
Compare 5 CNC/VMC training institutes in Bangalore.
The agent must perform at least three visible browser actions, such as search, filter, sort, open product/detail pages, switch tabs, expand hidden content, or submit a form. Passive scraping from search snippets is not accepted.

The final output must include a structured comparison table and a replay viewer/report showing:

1. Original user goal
2. Planner DAG
3. Browser path chosen: extract / deterministic / a11y / vision / blocked
4. Browser actions taken
5. Screenshots or page-state logs
6. Extracted data
7. Final comparison table
8. Turn count and cost summary
The orchestrator must not be modified. Any new behavior must plug in through the skill catalogue or as a Browser skill extension.

User Goal

Planner

Researcher
Find candidate URLs

Browser Skill
Interact with website

Cheapest correct path?

Extract
Static page

Deterministic
CSS selectors

A11y
Accessibility tree

Vision
Set-of-marks

Gateway Blocked
Recover or report

Distiller

QA / Critic

Replay Viewer

Final Comparison Table

Submission: YouTube demo, GitHub repo, replay trace/log, final comparison output, and a short architecture note. Code: llm_gatewayV9 | Session9Code

Transcript

Video
Studio


GMeet


Previous
Session 8 - Multi-Agent DAG Orchestration (GRAPHS!)

Axiom — Learning OS1Password menu is available. Press down arrow to select.