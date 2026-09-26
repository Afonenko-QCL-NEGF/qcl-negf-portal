/** Build and test the browser bundle independently of the Python environment. */

async function run(command: string, args: string[]): Promise<void> {
  const result = await new Deno.Command(command, {
    args, stdin: 'null', stdout: 'inherit', stderr: 'inherit',
  }).spawn().status;
  if (!result.success) throw new Error(`${command} failed with exit status ${result.code}`);
}

await run('npm', ['--prefix', 'frontend', 'ci']);
await run('npm', ['--prefix', 'frontend', 'test']);
await run('npm', ['--prefix', 'frontend', 'run', 'build']);
