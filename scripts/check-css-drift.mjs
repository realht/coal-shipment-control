import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const inputPath = resolve(repoRoot, "app/static/css/tailwind.src.css");
const trackedOutputPath = resolve(repoRoot, "app/static/css/tailwind.css");

// Browserslist/caniuse-lite вморожен в бандл Tailwind v3 (node_modules/tailwindcss/peers)
// и обновляется только вместе с самим tailwindcss; 3.4.19 — последняя версия ветки 3.4.x,
// апгрейд до v4 вне рамок MVP. Официальный флаг подавляет предупреждение об устаревшей
// базе кросс-платформенно (Windows dev + Linux Docker), не влияя на генерируемый CSS.
// Детали и обоснование — DEC в docs/wiki/decisions.md (V20-TECH-3).
process.env.BROWSERSLIST_IGNORE_OLD_DATA = "1";

function runTailwind(tempOutputPath) {
  const isWindows = process.platform === "win32";
  const command = process.platform === "win32" ? "npx.cmd" : "npx";
  const args = [
    "--no-install",
    "tailwindcss",
    "-i",
    inputPath,
    "-o",
    tempOutputPath,
    "--minify",
  ];

  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      cwd: repoRoot,
      stdio: "inherit",
      shell: isWindows,
    });

    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolvePromise();
        return;
      }
      reject(new Error(`Tailwind CSS check build failed with exit code ${code}.`));
    });
  });
}

async function main() {
  const tempDir = await mkdtemp(join(tmpdir(), "coal-shipments-css-"));
  const tempOutputPath = join(tempDir, "tailwind.css");

  try {
    await runTailwind(tempOutputPath);

    const [generated, tracked] = await Promise.all([
      readFile(tempOutputPath),
      readFile(trackedOutputPath),
    ]);

    if (!generated.equals(tracked)) {
      console.error("Tailwind CSS drift detected.");
      console.error("Run `npm run build:css` and commit regenerated app/static/css/tailwind.css.");
      process.exitCode = 1;
      return;
    }

    console.log("Tailwind CSS is up to date.");
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
