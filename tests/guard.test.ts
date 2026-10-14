import type { On } from 'claude-code'
import { describe, expect, test } from 'claude-code/testing'
import { bashRefusal, globToRegExp, protectedPath, type RunMarker } from '../hooks/rules.ts'

const marker: RunMarker = {
  run_id: 'r1',
  branch: 'auto/r1',
  protected_branches: ['main', 'master'],
  protected_paths: ['.specify/memory/constitution.md', 'specs/*/spec.md', '.sdd/run.json'],
}

describe('rules', () => {
  test('globs match one segment with * and many with **', () => {
    expect(globToRegExp('specs/*/spec.md').test('specs/001-x/spec.md')).toBe(true)
    expect(globToRegExp('specs/*/spec.md').test('specs/001-x/plan.md')).toBe(false)
    expect(globToRegExp('a/**/b.md').test('a/x/y/b.md')).toBe(true)
  })

  test('protected paths match absolute and relative paths', () => {
    expect(protectedPath(marker, '/repo', '/repo/specs/001-x/spec.md')).toBe('specs/*/spec.md')
    expect(protectedPath(marker, '/repo', './.specify/memory/constitution.md')).toBe('.specify/memory/constitution.md')
    expect(protectedPath(marker, '/repo', '/repo/specs/001-x/tasks.md')).toBeUndefined()
  })

  test('destructive git is refused, ordinary git is not', () => {
    for (const cmd of ['git push origin auto/r1', 'git -C x push', 'git reset --hard HEAD~1', 'git rebase main',
      'git commit --amend -m x', 'git branch -D old', 'git clean -fd', 'git switch main', 'git checkout -b y',
      'git commit --no-verify -m x', 'cd a && git push']) {
      expect(bashRefusal(marker, cmd, 'auto/r1')).toBeDefined()
    }
    for (const cmd of ['git status', 'git add -- a.py', 'git commit -m "feat(T1): x"', 'git checkout -- a.py',
      'git stash push -u -m x', 'git -C backend status', 'uv run pytest -q']) {
      expect(bashRefusal(marker, cmd, 'auto/r1')).toBeUndefined()
    }
  })

  test('commits on a protected branch are refused', () => {
    expect(bashRefusal(marker, 'git commit -m x', 'main')).toContain('protected branch main')
  })

  test('shell writes to protected files are refused', () => {
    expect(bashRefusal(marker, "sed -i '' s/a/b/ specs/001-x/spec.md", 'auto/r1')).toBeDefined()
    expect(bashRefusal(marker, 'echo x > .specify/memory/constitution.md', 'auto/r1')).toBeDefined()
    expect(bashRefusal(marker, 'cat specs/001-x/spec.md', 'auto/r1')).toBeUndefined()
    expect(bashRefusal(marker, 'tee -a specs/001-x/spec.md < x', 'auto/r1')).toBeDefined()
    expect(bashRefusal(marker, 'cp /tmp/x ./specs/001-x/spec.md', 'auto/r1')).toBeDefined()
    expect(bashRefusal(marker, 'rm -f /repo/.sdd/run.json', 'auto/r1', '/repo')).toBeDefined()
  })

  test('reads that redirect elsewhere are not writes', () => {
    for (const cmd of [
      'cat specs/001-x/*.md .specify/memory/constitution.md 2>/dev/null; ls src',
      'ls -R specs 2>&1; cat .specify/memory/constitution.md; git status',
      'grep -n FR specs/001-x/spec.md > /tmp/out.txt',
      "sed -n '1,20p' specs/001-x/spec.md",
      'python3 -m unittest -v 2>&1 | tail -20',
    ]) {
      expect(bashRefusal(marker, cmd, 'auto/r1', '/repo')).toBeUndefined()
    }
  })
})

describe('hooks', () => {
  const run = JSON.stringify(marker)

  const refused = (r: { isError?: boolean; deny?: string }) => r.isError === true || r.deny !== undefined

  function engineBelow(on: On, active: boolean, branch = 'auto/r1') {
    on('session.root', () => ({ value: '/repo' }))
    on('fs.read', ($, e) =>
      active && e.path === '/repo/.sdd/run.json' ? { value: run } : { deny: 'ENOENT' })
    on('process.run', () => ({ value: { exitCode: 0, stdout: `${branch}\n`, stderr: '' } }) as never)
    on('tool.call', () => ({ result: { text: 'ran' } }))
  }

  test('a push is denied while a run is active', async ($, on) => {
    engineBelow(on, true)
    const result = await $.tool.call({ tool: 'Bash', command: 'git push' })
    expect(refused(result)).toBe(true)
  })

  test('a push passes when no run is active', async ($, on) => {
    engineBelow(on, false)
    const result = await $.tool.call({ tool: 'Bash', command: 'git push' })
    expect(refused(result)).toBe(false)
  })

  test('editing spec.md is denied during a run', async ($, on) => {
    engineBelow(on, true)
    const result = await $.tool.call({ tool: 'Edit', file_path: '/repo/specs/001-x/spec.md', old_string: 'a', new_string: 'b' })
    expect(refused(result)).toBe(true)
  })

  test('a commit on main is denied during a run', async ($, on) => {
    engineBelow(on, true, 'main')
    const result = await $.tool.call({ tool: 'Bash', command: 'git commit -m x' })
    expect(refused(result)).toBe(true)
  })

  test('the main loop may remove the run marker; a subagent may not', async ($, on) => {
    engineBelow(on, true)
    expect(refused(await $.tool.call({ tool: 'Bash', command: 'rm .sdd/run.json' }))).toBe(false)
    expect(refused(await $.tool.call({ tool: 'Bash', command: 'rm .sdd/run.json', agentId: 'a1' } as never))).toBe(true)
  })
})
