// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Shennoter
import { build } from "esbuild";
import { readFile, writeFile, readdir, stat, copyFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const output = resolve(root, "../views/preview/app.js");
await copyFile(
  resolve(root, "../logo.png"),
  resolve(root, "../views/preview/logo.png"),
);
const result = await build({
  absWorkingDir: root,
  entryPoints: ["src/main.jsx"],
  outfile: output,
  bundle: true,
  format: "esm",
  target: ["chrome117", "firefox121", "safari17"],
  jsx: "automatic",
  minify: true,
  metafile: true,
  sourcemap: false,
  legalComments: "external",
  define: { "process.env.NODE_ENV": '"production"' },
  banner: {
    js: "// SPDX-License-Identifier: AGPL-3.0-only\n// Copyright (C) 2026 Shennoter; third-party notices: THIRD_PARTY_LICENSES.txt and app.js.LEGAL.txt",
  },
});
const packages = new Map();
for (const source of Object.keys(result.metafile.inputs)) {
  if (!source.includes("node_modules")) continue;
  let directory = dirname(resolve(root, source));
  while (directory !== dirname(directory)) {
    try {
      const metadata = JSON.parse(
        await readFile(join(directory, "package.json"), "utf8"),
      );
      if (metadata.name && metadata.version) {
        packages.set(`${metadata.name}@${metadata.version}`, {
          directory,
          metadata,
        });
        break;
      }
    } catch {}
    directory = dirname(directory);
  }
}
const notices = [];
const libraries = [];
for (const [name, { directory, metadata }] of [...packages].sort(([a], [b]) =>
  a.localeCompare(b),
)) {
  const license = (await readdir(directory)).find((file) =>
    /^licen[sc]e(?:\..*)?$/i.test(file),
  );
  const text = license
    ? (await readFile(join(directory, license), "utf8")).replaceAll(
        "\r\n",
        "\n",
      )
    : `SPDX: ${metadata.license || "See package source"}\n`;
  notices.push(
    `${"=".repeat(72)}\n${name}\nLicense: ${metadata.license}\n\n${text}`,
  );
  libraries.push({
    name: metadata.name,
    version: metadata.version,
    license: metadata.license,
  });
}
await writeFile(
  resolve(root, "../views/preview/THIRD_PARTY_LICENSES.txt"),
  notices.join("\n\n"),
);
const bytes = await readFile(output);
await writeFile(
  resolve(root, "../views/preview/build-info.json"),
  JSON.stringify(
    {
      ui: "Material UI",
      libraries,
      javascript: {
        file: "app.js",
        bytes: bytes.length,
        sha256: createHash("sha256").update(bytes).digest("hex"),
      },
    },
    null,
    2,
  ) + "\n",
);
console.log(
  `Built Material UI dashboard: ${(await stat(output)).size} bytes, ${libraries.length} licensed packages.`,
);
