# Screenshots

These images show the real frontend with a demo account and synthetic posts.
The capture fixture mocks every API response and blocks requests outside its
reserved `.test` origin. It does not start the app, read `.env` or access the
archive/database. No X credentials or credits are needed. The sample profile photo is served locally;
its source and license are in [scripts/fixtures/README.md](../../scripts/fixtures/README.md).

## Recreate

Run from the repository root with Node 20+ and Chrome installed. The project's
normal frontend checks require Node 24.15+. The capture tool is optional; it is
not an app dependency.

```sh
mkdir -p output/playwright
npx --yes --package @playwright/cli@0.1.25 playwright-cli -s=readme-shots open
npx --yes --package @playwright/cli@0.1.25 playwright-cli -s=readme-shots run-code --filename scripts/capture_screenshots.cjs
npx --yes --package @playwright/cli@0.1.25 playwright-cli -s=readme-shots close
```

The browser runs headlessly with an isolated profile. All application requests
are fulfilled by the fixture using public `static/` files or synthetic responses;
there is no connection to a running instance. The `.test` address is intercepted,
not resolved. Unknown requests fail the capture instead of reaching the network.

Before capturing the runner and activity, the script checks post selection,
review without deletion, exact `DELETE` confirmation, a single mock confirmation
request, and completed results without post-read requests. It also checks runner
cooldown, grouped activity, outcome pills, browser errors and horizontal overflow.
These checks exercise the frontend against mocks; backend deletion and persistence
are covered separately by `make test`.

Review the generated images in `output/playwright/`, then copy the five approved
files into this directory:

```sh
cp output/playwright/sign-in-setup.png output/playwright/sign-in.png output/playwright/posts.png output/playwright/runner.png output/playwright/activity.png docs/screenshots/
```

The clock is fixed to 26 November 2026 for stable date groups and cooldown labels.
Sign-in is 1280 × 800 and app setup is 1280 × 900; Posts and Activity are 1440 × 1120; Runner is 1440 × 1000.
Only the viewport and demo responses are staged; app styles and markup are unchanged.
The additional review-modal capture stays in ignored output as a verification artifact.
