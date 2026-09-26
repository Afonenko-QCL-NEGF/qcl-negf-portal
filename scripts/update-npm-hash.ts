/** Derive Nix's npm cache hash from the authoritative npm lock file. */
const output = await new Deno.Command("prefetch-npm-deps", {
  args: ["frontend/package-lock.json"],
  stdin: "null",
  stdout: "piped",
  stderr: "inherit",
}).output();
if (!output.success) {
  throw new Error(`prefetch-npm-deps failed with exit status ${output.code}`);
}
const npmDepsHash = new TextDecoder().decode(output.stdout).trim();
if (!/^sha256-[A-Za-z0-9+/]{43}=$/.test(npmDepsHash)) {
  throw new Error("prefetch-npm-deps returned an invalid SHA-256 SRI hash");
}
await Deno.writeTextFile(
  "nix/generated-npm-hash.json",
  JSON.stringify({ npmDepsHash }, null, 2) + "\n",
);
