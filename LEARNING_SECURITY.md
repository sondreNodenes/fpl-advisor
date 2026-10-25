# Securing a small cloud app, explained

This is the story of a security pass on FPL Advisor, a small website that gives Fantasy Premier League advice. It runs on Google Cloud, it is public, and anyone on the internet can use it. It has no user accounts and no passwords, and it stores nothing private. So you might ask: what is there to protect? That question is exactly where a security pass starts.

## Start with a threat model, not a scanner

A threat model is a short, honest answer to four questions. What are we building? What could go wrong? What are we doing about it? And did we do a good job? It comes first because without it you do not know what you are protecting, so you cannot tell an important finding from noise.

For this project the valuable things, the assets, turned out to be less obvious than data. The most important asset is the cloud bill. Anyone who can make the service do expensive work, over and over, can spend my money. The next assets are the Google Cloud project itself, the service accounts that the code runs as, the storage bucket with the trained model, and the GitHub repository, because a push to the main branch deploys straight to production.

Then you draw trust boundaries. A trust boundary is a line where data or control passes from something you do not trust to something you do. Here there are a few: the internet talking to the web service, the web service talking to the Fantasy Premier League API, the service reading from storage, and GitHub Actions talking to Google Cloud. Most security bugs live on those lines.

To make sure you think about every kind of problem, a common checklist is STRIDE. It stands for Spoofing, pretending to be someone else; Tampering, changing data you should not; Repudiation, denying you did something; Information disclosure, leaking things; Denial of service, making the system unavailable or, in the cloud, expensive; and Elevation of privilege, getting more power than you should have. I wrote fourteen threats into a table, each with a likelihood, an impact, what already protects against it, and what I planned to do. Every later step in the pass points back to one of those rows. That is the real value: it turns a vague feeling of "be secure" into a list of specific jobs.

## What the scanners look for, and why they can be wrong

Next I recorded a baseline, the state before changing anything. Five kinds of scanner ran, and they now run on every push and once a week.

Gitleaks looks for secrets in the code and in the whole git history: API keys, passwords, private keys. History matters because deleting a key in a later commit does not remove it from the repository. Gitleaks found nothing at first. Later it raised an alarm on a value called GPG_KEY inside a report I had saved. That turned out to be the public fingerprint of the key that signs Python releases. It sits in the official Python container image so the image can check the Python download. That is a false positive: a pattern that looks like a secret but is not one. I allowed that exact value, in those report files only, and wrote down why.

pip-audit compares the exact versions of every Python library the project uses against a database of known vulnerabilities. It found none. That sounds great, but it only knows about vulnerabilities someone has already reported. A brand-new problem, or a malicious package that nobody has flagged yet, passes straight through. That is a miss, the opposite of a false positive.

Ruff with its security rules, borrowed from a tool called Bandit, reads the Python code and flags risky patterns, such as running a shell command or using assert. It reported 154 issues. 153 were asserts in the test files, which is simply how the testing framework works, so they were noise. One was real but small: the code started the git program by name, which means a tampered search path could make it run a different program. Static analysis is like a spell checker. It is fast and catches common slips, but it does not understand what your program is for.

Trivy scanned the container image, the packaged box the app ships in. It found 187 known vulnerabilities. 164 were in the Debian operating system packages underneath Python, and none of those had a fix available yet. Six were in pip, the Python installer, which the running app never uses. Seventeen were in uv, the build tool, which had been left inside the final image by accident. Trivy also scanned the Terraform files that describe the cloud setup, and flagged things like a storage bucket without versioning.

The big lesson about scanners is the difference between a count and a risk. 187 sounds alarming. But most of those were in programs the app never runs, and the one change that mattered most, removing build tools from the image, cleared 23 of them at once.

## SSRF, with a simple analogy

The website asks for your Fantasy team ID or a link to your team page. Any time a server accepts a link from a stranger, a classic attack comes to mind: Server-Side Request Forgery, or SSRF.

Imagine a hotel receptionist who will fetch any parcel you ask for. You cannot walk into the staff-only storeroom, but you can ask the receptionist to fetch something from there. If they do not check, they will. SSRF is the same trick on a server. The attacker cannot reach internal systems, but the server can. In Google Cloud the classic target is the metadata server at the address 169.254.169.254, which can hand out access tokens for the service account.

FPL Advisor was never actually vulnerable, because it never fetched the link you typed. It only pulled the digits out of it. But it was sloppy. It accepted any website address containing "/entry/" and a number, including the metadata address, and it even accepted Arabic-Indic digits, because a Python function called isdigit says yes to them. Now the rule is strict: either plain digits, or a link that matches the exact Fantasy Premier League address pattern. Anything else is rejected before any work is done. The server only ever talks to one fixed address, and it refuses redirects, because a redirect is just another way of saying "go fetch this other thing".

## Abuse and the bill

In the cloud, denial of service has a twin: denial of wallet. The service scales on demand, so a flood does not just slow it down, it can make it expensive. I measured this instead of guessing. I ran the container on my laptop with the same size as in the cloud, flooded it with fake visitors, and fed the measured requests per second into Google's official price list. If someone flooded the site from many addresses for a whole month, the bill could reach about 4,200 dollars with the original settings. After limiting it to one instance and compressing responses, the measured worst case is about 930 dollars. That is still not zero, and I wrote that down plainly. The budget alert only sends an email, it does not stop spending.

For a single attacker, rate limits now do the job: 120 requests a minute per address across the site, and 10 a minute for the expensive advice endpoint. Requests that are too large get rejected. At most two heavy calculations run at once, and anything that takes over a minute is cut off.

## Least privilege, and why it matters

Least privilege means every identity gets only the permissions it needs, and nothing more. Think of a building where the cleaner's key opens every door, including the server room. Nothing goes wrong until someone steals the cleaner's key.

The most serious finding in the whole pass came from reading the live permissions, not from any scanner. Google automatically gives a "default compute service account" the Editor role on new projects, which is power over nearly everything. None of my code used it, but anything deployed later without naming an account would quietly run as Editor. The fix removes that grant. I also narrowed the deploy account: instead of Cloud Run admin, it now gets the developer role plus a tiny custom role with exactly the two permissions Terraform needs to make the site public and let the scheduler start the nightly job.

## Workload Identity Federation instead of keys

GitHub Actions needs to deploy to Google Cloud. The old way is to create a key file for a service account and paste it into GitHub as a secret. That key works from anywhere, never expires unless you rotate it, and leaks easily.

Workload Identity Federation replaces the key with a trust relationship. When a GitHub workflow runs, GitHub gives it a signed, short-lived identity token that says which repository, which branch and which event it came from. Google checks the signature and then a rule I wrote, called the attribute condition. Only if the rule passes does Google hand out a short-lived token for the deploy account. There is no stored key to steal.

The attribute condition is the lock on that door. Without it, any GitHub repository could ask. Mine already required the right repository name and the main branch, so a pull request from a fork or a feature branch could not deploy. The pass tightened it further. Repository names can change hands: if I renamed my account, someone else could register the old name and create a repository with the same name. So the rule now checks GitHub's numeric repository ID and owner ID, which never change, plus the main branch. A Terraform check also stops anyone from accidentally widening the rule later.

## The pipeline is part of the attack surface

The build pipeline runs other people's code: GitHub Actions written by third parties, base images from Docker Hub. If an action's version tag is moved to point at malicious code, every repository using that tag runs it. So every action is now pinned to an exact commit, base images are pinned to an exact digest, and each workflow gets only read access, with the deploy job adding just the right to request an identity token. Dependabot opens update requests every week, so pinning does not mean falling behind. The deploy job now scans the image and refuses to push it if it has any fixable high or critical vulnerability.

## What this pass does not cover

It is important to say what was not done. There is no web application firewall, so a large distributed flood can still cost real money. There are no user accounts. Images are not signed and there is no software bill of materials. Rate limits live in memory, so they reset when the service restarts. Some changes to the cloud foundation need my own hands to apply, and they are written down as such. Nobody did a penetration test against the live service. Everything here was tested on my own machine and in my own project, and every number in the findings comes from a command anyone can rerun. That habit is the real takeaway: say what you checked, show how, and be honest about the rest.
