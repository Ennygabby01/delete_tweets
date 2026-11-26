export default [
  {
    files: ["static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: Object.fromEntries(
        [
          "window",
          "document",
          "navigator",
          "location",
          "history",
          "fetch",
          "crypto",
          "BroadcastChannel",
          "Event",
          "URL",
          "URLSearchParams",
          "setTimeout",
          "clearTimeout",
          "setInterval",
          "clearInterval",
          "ResizeObserver",
          "getComputedStyle",
        ].map((name) => [name, "readonly"]),
      ),
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": "error",
      "no-unreachable": "error",
      "no-dupe-keys": "error",
      "valid-typeof": "error",
    },
  },
];
