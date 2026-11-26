async function captureScreenshots(page) {
  // Serve only public frontend files; every API response below is synthetic.
  const origin = "https://tweet-deleter.test";
  const now = Date.parse("2026-11-26T10:30:00+01:00") / 1000;
  const errors = [],
    unexpected = [],
    calls = [];
  await page.unrouteAll({ behavior: "wait" });
  await page.setViewportSize({ width: 1440, height: 1120 });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.clock.install({ time: new Date(now * 1000) });
  const copy = [
    "The first version of our portfolio is live. A few late nights, plenty of coffee, and a lot learned along the way.",
    "Weekend reading: small details that make everyday software easier to use.",
    "An old side project, finally finished. Notes on what worked and what I would change next time.",
    "A little collection of useful resources for anyone building their first website.",
    "Today's reminder: save the working version before trying that one last improvement.",
    "Looking back at the first design for this project. The simplest ideas held up best.",
    "A few photos from a quiet afternoon walk. Sometimes stepping away is the most useful part of the day.",
    "The archive has become a time capsule of projects, bookmarks and unfinished ideas.",
    "Sharing the checklist I use before publishing a small project. Still adding to it as I go.",
    "Made time to revisit an old experiment this week. Happy to see how much has changed.",
  ];
  const posts = copy.slice(0, 5).map((text, index) => ({
    id: String(900000000000000000n + BigInt(index)),
    text,
    created_at:
      Date.parse(`2023-08-${String(24 - index).padStart(2, "0")}T12:00:00Z`) /
      1000,
    urls: ["https://example.com/journal"],
  }));
  const counts = (deleted, pending = 0, unavailable = 0) => ({
    deleted,
    pending,
    unavailable,
    failed: 0,
    unknown: 0,
    sending: 0,
  });
  const completed = {
    id: "a".repeat(32),
    status: "completed",
    planned: 3,
    created: now - 1800,
    counts: counts(2, 0, 1),
    note: "",
    schedule: null,
    remaining_selection: 0,
    post_order: "newest",
    items: posts.slice(0, 3).map((post, index) => ({
      ...post,
      status: index === 2 ? "unavailable" : "deleted",
      message: "",
    })),
  };
  const prior = {
    ...completed,
    id: "b".repeat(32),
    planned: 50,
    created: now - 86400,
    counts: counts(50),
    items: [],
  };
  const older = {
    ...completed,
    id: "c".repeat(32),
    planned: 44,
    created: now - 172800,
    counts: counts(44),
    items: [],
  };
  const scheduled = {
    ...completed,
    id: "d".repeat(32),
    status: "running",
    planned: 500,
    created: now - 3600,
    counts: counts(150, 350),
    schedule: { cap: 500, batch_size: 50, waiting_until: now + 600 },
    items: [],
  };
  let preview = null;
  let confirmed = false;
  const scenario = () =>
    new URL(page.url()).searchParams.get("demo") || "sign-in";
  const quota = () => ({
    limit: 50,
    remaining: scenario() === "runner" ? 0 : 50,
    can_plan: scenario() === "runner" ? 0 : 50,
    source: "x_response",
    resets: now + 600,
  });
  await page.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    const json = (data, status = 200) => route.fulfill({ status, json: data });
    if (url.origin !== origin) {
      unexpected.push(request.url());
      return route.abort("blockedbyclient");
    }
    const path = url.pathname;
    if (path.startsWith("/v1/")) {
      calls.push({
        method: request.method(),
        path,
        body: request.postDataJSON(),
      });
      if (path === "/v1/session" && request.method() === "GET")
        return json({
          authenticated: !["sign-in", "setup"].includes(scenario()),
          configured: scenario() !== "setup",
          profile: {
            name: "Alex Morgan",
            username: "alex_demo",
            profile_image_url: `${origin}/demo-avatar.jpg`,
          },
          csrf: "synthetic-demo-csrf",
          callback_url: "http://127.0.0.1:8000/v1/auth/callback",
          max_archive_bytes: 100000000,
          session_idle_seconds: 3600,
        });
      if (path === "/v1/session/activity" && request.method() === "POST")
        return json({ ok: true });
      if (path === "/v1/workspace" && request.method() === "GET")
        return json({
          stats: {
            total: 1280,
            matched: scenario() === "runner" ? 350 : 248,
            deleted: scenario() === "runner" ? 246 : 96,
            unavailable: 1,
          },
          filters: { sites: ["example.com"], keywords: [] },
          quota: quota(),
          runs: [
            ...(scenario() === "runner"
              ? [scheduled]
              : preview
                ? [preview]
                : []),
            completed,
            prior,
            older,
          ],
          sync: {
            status: "idle",
            retry_after: 0,
            completed: 0,
            has_older: false,
          },
          preferences: { allow_post_reads: false, post_order: "newest" },
          runner_limits: {
            max_posts: 5000,
            batch_size: 50,
            fallback_window_seconds: 900,
          },
        });
      if (path === "/v1/posts" && request.method() === "GET")
        return json({ posts, total: 248, page: 1, pages: 5 });
      if (path === "/v1/runs" && request.method() === "GET")
        return json({
          runs: [...(confirmed ? [preview] : []), completed, prior, older],
          total: confirmed ? 4 : 3,
          page: 1,
          pages: 1,
        });
      if (path === "/v1/runs/preview" && request.method() === "POST") {
        const body = request.postDataJSON();
        const selected = body.all_matching
          ? posts
          : posts.filter((post) => body.ids.includes(post.id));
        preview = {
          ...completed,
          id: "e".repeat(32),
          status: "draft",
          planned: selected.length,
          counts: counts(0, selected.length),
          created: now,
          quota: quota(),
          items: selected.map((post) => ({
            ...post,
            status: "pending",
            message: "",
          })),
        };
        return json(preview);
      }
      if (
        path === `/v1/runs/${"e".repeat(32)}/confirm` &&
        request.method() === "POST"
      ) {
        if (!preview || request.postDataJSON().confirmation !== "DELETE")
          return json({ detail: "Confirmation required." }, 422);
        confirmed = true;
        preview.status = "completed";
        preview.counts = counts(preview.planned);
        preview.items.forEach((item) => {
          item.status = "deleted";
        });
        return json(preview);
      }
      if (/^\/v1\/runs\/[a-e]{32}$/.test(path) && request.method() === "GET") {
        const run = [completed, prior, older, scheduled, preview].find(
          (run) => run?.id === path.split("/").at(-1),
        );
        if (run) return json(run);
      }
      unexpected.push(`${request.method()} ${path}`);
      return json({ detail: "Not part of the screenshot fixture." }, 501);
    }
    if (request.method() !== "GET") {
      unexpected.push(`${request.method()} ${path}`);
      return route.abort("blockedbyclient");
    }
    if (path === "/demo-avatar.jpg")
      return route.fulfill({
        path: "scripts/fixtures/demo-avatar.jpg",
        contentType: "image/jpeg",
      });
    if (path === "/")
      return route.fulfill({
        path: "static/index.html",
        contentType: "text/html",
      });
    if (
      /^\/static\/(?:js\/[a-z-]+\.js|(?:app|tokens)\.css|(?:icons|logo)\.svg)$/.test(
        path,
      )
    )
      return route.fulfill({
        path: path.slice(1),
        contentType: path.endsWith(".js")
          ? "text/javascript"
          : path.endsWith(".css")
            ? "text/css"
            : "image/svg+xml",
      });
    unexpected.push(path);
    return route.abort("blockedbyclient");
  });
  await page.goto(`${origin}/?demo=sign-in`);
  await page.locator("#sign-in").waitFor({ state: "visible" });
  const check = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const capture = async (name) => {
    await page.evaluate(() => document.fonts.ready);
    if (await page.locator("#workspace").isVisible()) {
      await page.locator("#account-avatar img").waitFor({ state: "visible" });
      check(
        await page
          .locator("#account-avatar img")
          .evaluate((image) => image.complete && image.naturalWidth > 0),
        "Profile image must be loaded",
      );
    }
    check(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "Horizontal overflow",
    );
    await page.screenshot({
      path: `output/playwright/${name}.png`,
      animations: "disabled",
      caret: "hide",
    });
  };
  await capture("sign-in");
  await page.goto(`${origin}/?demo=setup`);
  await page.locator("#login-setup").waitFor({ state: "visible" });
  check(
    await page.locator("#connect").isDisabled(),
    "Unconfigured sign-in must be disabled",
  );
  await page.locator('[aria-controls="login-setup-content"]').click();
  await page.locator("#login-setup-content").waitFor({ state: "visible" });
  check(
    (await page.locator("#login-callback").inputValue()) ===
      "http://127.0.0.1:8000/v1/auth/callback",
    "Setup callback missing",
  );
  await page.mouse.move(1430, 1110);
  await capture("sign-in-setup");
  await page.goto(`${origin}/?demo=posts`);
  await page.locator("#workspace").waitFor({ state: "visible" });
  check(
    (await page.locator(".post-row").count()) === posts.length,
    "Demo posts did not render",
  );
  for (let index = 0; index < 3; index++)
    await page.locator(".post-checkbox").nth(index).check();
  check(
    (await page.locator("#selection-count").textContent()) === "3 selected",
    "Selection count mismatch",
  );
  check(await page.locator("#review").isEnabled(), "Review should be enabled");
  await page.mouse.move(1430, 1110);
  await capture("posts");

  // Verify that review and exact confirmation gate the mock deletion.
  await page.locator("#review").click();
  await page.locator("#confirmation").waitFor({ state: "visible" });
  check(!confirmed, "Review must not delete");
  check(
    preview.planned === 3 && preview.counts.pending === 3,
    "Preview lost selected posts",
  );
  check(
    await page.locator("#confirm-delete").isDisabled(),
    "Empty confirmation must be disabled",
  );
  await page.locator("#confirmation-input").fill("delete");
  check(
    await page.locator("#confirm-delete").isDisabled(),
    "Confirmation must match exactly",
  );
  await capture("review");
  await page.locator("#confirmation-input").fill("DELETE");
  await page.locator("#confirm-delete").click();
  await page.locator("#confirmation").waitFor({ state: "hidden" });
  await page.locator("#run-detail").waitFor({ state: "visible" });
  check(
    confirmed && preview.counts.deleted === 3,
    "Mock deletion did not finish",
  );
  check(
    (await page.locator("#run-status").textContent()) === "Finished",
    "Final status missing",
  );
  check(
    calls.filter((call) => call.path.endsWith("/confirm")).length === 1,
    "Confirmation must be sent once",
  );
  check(
    !calls.some((call) => call.path === "/v1/posts/sync"),
    "Archive deletion must not trigger reads",
  );

  preview = null;
  confirmed = false;
  await page.goto(`${origin}/?demo=runner#/runner`);
  await page.locator("#runner-current").waitFor({ state: "visible" });
  check(
    (await page.locator("#runner-status").textContent()) === "Cooling down",
    "Runner cooldown missing",
  );
  check(
    (await page.locator("#runner-progress-count").textContent()) ===
      "150 of 500 processed",
    "Runner progress mismatch",
  );
  await capture("runner");
  await page.goto(`${origin}/?demo=activity#/activity/${completed.id}`);
  await page.locator("#run-detail").waitFor({ state: "visible" });
  check(
    (await page.locator(".history-group").count()) === 3,
    "Activity date groups missing",
  );
  check(
    await page
      .locator("#run-items .badge")
      .allTextContents()
      .then(
        (labels) => labels.join(",") === "Deleted,Deleted,Unavailable on X",
      ),
    "Activity outcomes missing",
  );
  await capture("activity");
  check(errors.length === 0, `Browser errors: ${errors.join("; ")}`);
  check(
    unexpected.length === 0,
    `Unexpected requests: ${unexpected.join("; ")}`,
  );
  return {
    screenshots: ["sign-in", "sign-in-setup", "posts", "runner", "activity"],
    mockDeletion: "passed",
    externalRequests: 0,
    browserErrors: errors.length,
  };
}
