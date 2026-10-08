/** Build and test the browser bundle independently of the Python environment. */

// npm and its Node/build-tool children need executable lookup, user cache and
// the explicitly configured HTTP route, but not the runner's native loader env.
const environment: Record<string, string> = {};
for (const name of [
  'PATH', 'HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
  'http_proxy', 'https_proxy', 'no_proxy',
]) {
  const value = Deno.env.get(name);
  if (value !== undefined) environment[name] = value;
}

async function run(command: string, args: string[]): Promise<void> {
  const result = await new Deno.Command(command, {
    args, clearEnv: true, env: environment, stdin: 'null', stdout: 'inherit', stderr: 'inherit',
  }).spawn().status;
  if (!result.success) throw new Error(`${command} failed with exit status ${result.code}`);
}

await run('npm', ['--prefix', 'frontend', 'ci']);
await run('npm', ['--prefix', 'frontend', 'test']);
await run('npm', ['--prefix', 'frontend', 'run', 'build']);
