"""Requirement (Connections G2 pod-grant-isolation): if ``HOME`` is remapped
for ANY process, the real passwd home's sensitive paths (``~/.aws``,
``~/.ssh``, ``~/.kirocrew*``) must STILL be denied by ``security.py``'s
matchers inside that process. A remap that unfences the real home is a
rejected design.

Why this matters for the pod ``HOME`` remap
(``acp.client._apply_pod_home_remap``): ``security.py``'s sensitive-path
matchers run inside the GATEWAY process, evaluating a tool call's ARGUMENTS
against the gateway's own ``Path.home()`` -- the spawned kiro-cli child's
remapped ``HOME`` never reaches this code, because the gateway process's
``os.environ["HOME"]`` is never touched by ``build_pod_env`` or by
``_apply_pod_home_remap`` (which mutates only the CHILD's env dict handed to
``create_subprocess_exec``/``create_subprocess_limited``, not the gateway's
own ``os.environ``).

These tests pin that property directly: a fenced path resolved against the
gateway's OWN home stays denied regardless of what ``HOME`` a spawned
child happens to run under.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kiro_crew import security
from kiro_crew.acp.client import _apply_pod_home_remap


def _pin_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> Path:
    """Pin this process's home to *home* on every platform, and return it as
    ``Path.home()`` resolves it.

    ``USERPROFILE`` is set alongside ``HOME`` because Windows ``Path.home()``
    reads that one and never ``HOME`` -- pinning only ``HOME`` leaves the
    matcher anchored on the real runner profile there, which is what made these
    tests fail on the Windows shard while passing on Linux. Mirrors the autouse
    fixture in ``test_pod.py``, which pins both for the same reason.

    **Both HOME-derived caches in ``security`` are reset**, and that is what makes
    a pinned home actually reach the matcher rather than only the environment.
    ``is_sensitive_bash_command`` runs several passes over different derivations of
    ``Path.home()``:

    * ``_SENSITIVE_RE`` (pass 1, the fast-path regex) is a PROCESS GLOBAL built
      once -- ``if _SENSITIVE_RE is None`` -- with no TTL and no invalidation. It
      captures ``Path.home()`` at first use, so once ANY earlier test in the same
      xdist worker has called a matcher, a later ``monkeypatch.setenv("HOME", ...)``
      is invisible to that pass forever.
    * ``_home_targets_cache`` (the later passes) is TTL-bounded and keyed on the
      resolved roots, so it does pick up a new home -- and it is the cache the rest
      of ``test_security.py`` already clears by hand, which is the established seam
      this follows.

    That asymmetry is exactly why this failed ONLY on Windows: on Linux the
    target-set passes rescued the stale pass-1 regex (verified -- priming the
    global before moving HOME does not change the Linux verdict), while on the
    Windows runner they did not. The Windows pattern itself is NOT the gap: built
    with a Windows-shaped home and cleared caches, it matches
    ``C:\\...\\real-home\\.aws\\credentials`` (the builder has a full
    ``win_sep``/``win_gsep`` branch). Nothing in production depends on the reset --
    the gateway's own HOME does not move under it, and ``_apply_pod_home_remap``
    changes only a CHILD's environment -- so this is a test-pinning fix, not a
    matcher fix.

    Also creates the directory, so ``resolve()`` is well defined on every platform,
    and returns the resolved value because the target set is anchored on
    ``Path.home().resolve()``.
    """
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Order matters: set the env FIRST, then drop both derivations of it.
    monkeypatch.setattr(security, "_SENSITIVE_RE", None, raising=False)
    security._home_targets_cache.clear()
    return Path.home().resolve()


class TestGatewayHomeIsIndependentOfAChildsRemappedHome:
    def test_apply_pod_home_remap_never_touches_process_environ(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The remap operates on a plain dict handed to the child spawn call
        -- never on os.environ, which is what security.py's Path.home() calls
        resolve against inside the gateway's own process."""
        real_home = _pin_home(monkeypatch, tmp_path / "real-home")
        gateway_home_before = Path.home()

        child_env = {
            "HOME": str(real_home),
            "KIROCREW_POD": "1",
            "KIROCREW_OS_HOME": str(tmp_path / "pod-os-home"),
        }
        _apply_pod_home_remap(child_env, pod_home_remap=True)

        assert child_env["HOME"] == str(tmp_path / "pod-os-home")
        # The gateway's OWN Path.home() -- what security.py's matchers read --
        # is completely unaffected by mutating the child's env dict.
        assert Path.home() == gateway_home_before == real_home

    def test_sensitive_paths_stay_denied_against_the_gateways_own_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Simulates the pod boot ordering: a KIROCREW_POD/KIROCREW_OS_HOME
        pair is present in the GATEWAY's own os.environ too (build_pod_env
        sets both on the whole pod gateway process), yet a tool call naming
        the real ~/.aws/credentials must still be denied by is_sensitive_path
        -- the pod anchor ADDS a fenced root, it never removes the real one."""
        real_home = _pin_home(monkeypatch, tmp_path / "real-home")
        monkeypatch.setenv("KIROCREW_POD", "1")
        monkeypatch.setenv("KIROCREW_OS_HOME", str(tmp_path / "pod-os-home"))

        assert security.is_sensitive_path(str(real_home / ".aws" / "credentials")) is True
        assert security.is_sensitive_path(str(real_home / ".ssh" / "id_rsa")) is True
        assert security.is_sensitive_path("~/.aws/credentials") is True

    def test_the_relocated_pod_home_is_fenced_too(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The regression all three review lanes converged on: relocating the
        credential store must not move it OUT from under the fence.

        `_seed_pod_os_home` copies the operator's real SSO bearer token into
        `<pod home>/os-home/.aws/sso/cache`, and a pod-spawned child's `$HOME`
        is that tree -- so if `is_sensitive_path` anchored `.aws` only under the
        real home, an agent inside a pod could read a verbatim copy of the
        operator's identity token at the pod-path spelling while the identical
        bytes at `~/.aws` were refused. `KIROCREW_OS_HOME` is therefore anchored
        as an alternate home root, and EVERY fenced entry re-anchors under it,
        not merely `.aws`."""
        pod_os_home = tmp_path / "pod-os-home"
        _pin_home(monkeypatch, tmp_path / "real-home")
        monkeypatch.setenv("KIROCREW_POD", "1")
        monkeypatch.setenv("KIROCREW_OS_HOME", str(pod_os_home))

        # The seeded host SSO token, at the pod-path spelling.
        assert (
            security.is_sensitive_path(
                str(pod_os_home / ".aws" / "sso" / "cache" / "kiro-auth-token.json")
            )
            is True
        )
        # A pod-minted MCP grant pair lands in the same directory.
        assert security.is_sensitive_path(str(pod_os_home / ".aws" / "credentials")) is True
        # The relocation moves the WHOLE home, so every other fenced entry
        # follows it -- not just the one subtree the token happens to live in.
        assert security.is_sensitive_path(str(pod_os_home / ".ssh" / "id_ed25519")) is True

    def test_a_remapped_child_env_home_does_not_leak_into_the_matcher(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The child's env dict is not an input to the matcher: `is_sensitive_path`
        takes no environment, so the real home stays fenced regardless of what
        HOME the child was handed. The pod tree is fenced too, but by the
        os.environ-level anchor rather than by this dict."""
        real_home = _pin_home(monkeypatch, tmp_path / "real-home")

        child_env = {
            "HOME": str(real_home),
            "KIROCREW_POD": "1",
            "KIROCREW_OS_HOME": str(tmp_path / "pod-os-home"),
        }
        _apply_pod_home_remap(child_env, pod_home_remap=True)
        assert child_env["HOME"] == str(tmp_path / "pod-os-home")

        assert security.is_sensitive_path(str(real_home / ".ssh" / "id_ed25519")) is True
        # No KIROCREW_OS_HOME in THIS process's environ, so the pod spelling is
        # not anchored here -- which is why build_pod_env sets it on the pod
        # gateway itself, covered by the test above.
        assert (
            security.is_sensitive_path(str((tmp_path / "pod-os-home") / ".ssh" / "id_ed25519"))
            is False
        )

    def test_is_sensitive_bash_command_also_keys_on_the_gateways_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        real_home = _pin_home(monkeypatch, tmp_path / "real-home")
        monkeypatch.setenv("KIROCREW_POD", "1")

        # Built with the running OS's separator: a hardcoded POSIX spelling
        # matches nothing on Windows, where every candidate form the matcher
        # derives is backslash-separated.
        target = str(real_home / ".aws" / "credentials")

        # The path gate first. It shares the home-derived target set with the bash
        # matcher's later passes, so if THIS holds the pinned home did reach the
        # matcher -- which makes the next assertion a statement about the bash
        # surface alone rather than about whether the pin took.
        assert security.is_sensitive_path(target) is True
        assert security.is_sensitive_bash_command(f"cat {target}") is not None

    def test_the_pinned_home_reaches_the_fast_path_regex_too(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`_SENSITIVE_RE` is a process global with no TTL and no invalidation, so
        a matcher call from ANY earlier test in this worker freezes it on the real
        runner home and a later `setenv("HOME", ...)` never reaches pass 1. On
        Linux the target-set passes cover for that; on the Windows shard they did
        not, which is why only Windows failed. Pins that `_pin_home` drops the
        global: prime it first, exactly as a neighbouring test would."""
        security.is_sensitive_bash_command("cat /etc/hostname")  # prime pass 1
        assert security._SENSITIVE_RE is not None

        real_home = _pin_home(monkeypatch, tmp_path / "real-home")

        assert security._SENSITIVE_RE is None, "_pin_home must drop the primed regex"
        target = str(real_home / ".aws" / "credentials")
        assert security.is_sensitive_bash_command(f"cat {target}") is not None


class TestPodMintedGrantsAreFencedFromToolCalls:
    # The two-audience split, pinned.
    #
    # The sandbox mask deliberately CARVES OUT <os-home>/.aws so the pod's own
    # kiro-cli can read and WRITE its MCP OAuth grants there -- there is no env
    # lever that relocates them, and masking the tree empty discards every grant
    # the pod mints. That leaves one audience to fence: an agent TOOL call. It is
    # fenced HERE, in-band, because KIROCREW_OS_HOME is anchored as an alternate
    # $HOME and every fenced entry is re-anchored under it.
    #
    # Consequence worth stating: a pod's posture is strictly NARROWER than the
    # non-pod baseline, where the standard tier leaves the REAL ~/.aws visible to
    # tools (sandbox.py's _STANDARD_DIRS omits .aws so credential_process can
    # reach Bedrock auth). A pod denies the pod-local tree at the gate on top of
    # that. Red-first in both matcher forms and both separators.

    @staticmethod
    def _grant(os_home: Path) -> Path:
        return os_home / ".aws" / "sso" / "cache" / (("a" * 64) + ".token.json")

    def test_a_tool_path_read_of_a_pod_minted_grant_is_denied(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        os_home = tmp_path / "pod-os-home"
        monkeypatch.setenv("KIROCREW_POD", "1")
        monkeypatch.setenv("KIROCREW_OS_HOME", str(os_home))

        grant = self._grant(os_home)
        assert security.is_sensitive_path(str(grant)) is True
        assert security.is_sensitive_path(str(os_home / ".aws" / "sso" / "cache")) is True
        assert security.is_sensitive_path(str(os_home / ".aws")) is True

    def test_a_tool_bash_read_of_a_pod_minted_grant_is_denied(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        os_home = tmp_path / "pod-os-home"
        monkeypatch.setenv("KIROCREW_POD", "1")
        monkeypatch.setenv("KIROCREW_OS_HOME", str(os_home))

        grant = self._grant(os_home)
        for command in (
            f"cat {grant}",
            f"cp {grant} /tmp/exfil",
            f"ls {grant.parent}",
        ):
            assert security.is_sensitive_bash_command(command), command

    def test_a_non_pod_gateway_target_set_is_unchanged(self, monkeypatch) -> None:
        # No marker, no os-home: the fence is exactly the real home's.
        monkeypatch.delenv("KIROCREW_POD", raising=False)
        monkeypatch.delenv("KIROCREW_OS_HOME", raising=False)

        assert security.is_sensitive_path("~/.aws/credentials") is True
        assert security.is_sensitive_path("/tmp/not-a-secret") is False

    def test_the_target_set_carries_both_separator_joins(self, tmp_path: Path, monkeypatch) -> None:
        """The Windows fix, pinned at the BUILDER where it is platform-independent.

        The os-home targets used to be joined with the RUNNING OS's separator only.
        That is not enough on the bash surface: ``_shape_path_token`` normalises a
        token's backslashes to forward slashes before comparing, so on Windows the
        target was ``<os-home>\\.aws`` while every candidate form was
        ``<os-home>/.aws`` -- they never compared equal, and shard 3 failed there
        while passing on POSIX. The re-anchor now emits BOTH joins.

        Asserted on the target set rather than through a hand-backslashed absolute
        path: the ROOT's own separators are whatever the platform produced, and
        rewriting those manufactures a spelling no platform emits (this suite's
        convention -- build targets with the running OS's separator). What the fix
        owns is the join between the root and the fenced entry, which is what this
        checks.
        """
        os_home = tmp_path / "pod-os-home"
        monkeypatch.setenv("KIROCREW_POD", "1")
        monkeypatch.setenv("KIROCREW_OS_HOME", str(os_home))

        targets = security._home_dir_targets(security._SENSITIVE_HOME_DIRS)
        root = str(os_home).rstrip("/\\").casefold()

        assert f"{root}/.aws" in targets, "forward-slash join missing"
        assert f"{root}\\.aws" in targets, "backslash join missing (the Windows gap)"
        # Multi-segment entries too -- those are the ones that split on "/".
        cache = "/".join([root, ".aws", "sso", "cache"])
        assert cache in targets or f"{root}/.aws" in targets

    def test_the_native_spelling_is_still_fenced_on_this_platform(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Widening must not disturb the form that already worked."""
        os_home = tmp_path / "pod-os-home"
        monkeypatch.setenv("KIROCREW_POD", "1")
        monkeypatch.setenv("KIROCREW_OS_HOME", str(os_home))

        grant = self._grant(os_home)

        assert security.is_sensitive_path(str(grant)) is True
        assert security.is_sensitive_bash_command(f"cat {grant}")


class TestTheStagedIdentityStoreIsFencedFromToolCalls:
    """The second two-audience tree, same split as the grant store.

    ``_seed_pod_os_home`` stages the agent runtime's identity store into the pod
    home so kiro-cli can sign in -- and that store is a BEARER-TOKEN DATABASE. The
    harness must keep reading it (the mount stays open; masking it empty is what
    broke sign-in two rounds ago), so the fence is gate-layer only: an agent TOOL
    call naming any path under it is refused in-band.

    Derived, not enumerated. ``identity_stores.fenced_home_dirs()`` -- the SAME
    table ``store_mappings`` seeds from -- is spliced into
    ``security._SENSITIVE_HOME_DIRS``, and ``_home_dir_targets_uncached``
    re-anchors EVERY ``home_dirs`` entry under ``KIROCREW_OS_HOME``. So a store row
    added to that table gains the real-home fence AND the pod-home fence with no
    second edit, which is the property these tests pin.
    """

    @staticmethod
    def _staged_db(os_home: Path) -> Path:
        return os_home / ".local" / "share" / "kiro-cli" / "data.sqlite3"

    def test_every_table_row_is_in_the_fence(self) -> None:
        """One table. A row that is not fenced would be staged and readable."""
        from kiro_crew import identity_stores

        fenced = set(security._SENSITIVE_HOME_DIRS)
        missing = [d for d in identity_stores.fenced_home_dirs() if d not in fenced]
        assert not missing, f"store rows staged but not fenced: {missing}"

    def test_a_tool_path_read_of_the_staged_store_is_denied(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        os_home = tmp_path / "pod-os-home"
        monkeypatch.setenv("KIROCREW_POD", "1")
        monkeypatch.setenv("KIROCREW_OS_HOME", str(os_home))

        db = self._staged_db(os_home)
        assert security.is_sensitive_path(str(db)) is True
        assert security.is_sensitive_path(str(db.parent)) is True
        # The sidecars carry the same bytes; fencing only the .sqlite3 name would
        # leave the WAL readable, which is the whole database in practice.
        assert security.is_sensitive_path(f"{db}-wal") is True
        assert security.is_sensitive_path(f"{db}-shm") is True

    def test_a_tool_bash_read_of_the_staged_store_is_denied(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        os_home = tmp_path / "pod-os-home"
        monkeypatch.setenv("KIROCREW_POD", "1")
        monkeypatch.setenv("KIROCREW_OS_HOME", str(os_home))

        db = self._staged_db(os_home)
        for command in (f"cat {db}", f"cp {db} /tmp/exfil", f"sqlite3 {db} .dump"):
            assert security.is_sensitive_bash_command(command), command

    def test_every_platform_layout_is_fenced_under_the_pod_home(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """macOS and Windows layouts too -- the pod's platform is not the fence's."""
        from kiro_crew import identity_stores

        os_home = tmp_path / "pod-os-home"
        monkeypatch.setenv("KIROCREW_POD", "1")
        monkeypatch.setenv("KIROCREW_OS_HOME", str(os_home))

        for relative in identity_stores.fenced_home_dirs():
            target = os_home / Path(*relative.split("/")) / "data.sqlite3"
            assert security.is_sensitive_path(str(target)) is True, relative

    def test_the_mount_stays_open_so_the_harness_can_still_sign_in(self) -> None:
        """Gate-layer ONLY -- the launcher masks are unchanged.

        The repo already asserts this for the REAL home
        (``test_the_agent_runtime_auth_stores_stay_visible`` in the sandbox-mask
        suite): the runtime resolves its own access token from that store while
        running inside the sandbox, so a mask entry covering it breaks sign-in
        rather than protecting anything. This is the pod-home variant of the same
        assertion -- the fence added above must not have leaked into a mask tier.
        """
        from kiro_crew import identity_stores, sandbox

        store_dirs = set(identity_stores.fenced_home_dirs())
        for tier_name in ("_STRICT_DIRS", "_CC_DIRS", "_STANDARD_DIRS"):
            tier = getattr(sandbox, tier_name, None)
            if tier is None:  # pragma: no cover - tier renamed
                continue
            covering = [
                entry
                for entry in tier
                if entry in store_dirs or any(store.startswith(f"{entry}/") for store in store_dirs)
            ]
            assert not covering, f"{tier_name} masks the identity store: {covering}"


class TestAWindowsNativePathSurvivesBashTokenization:
    r"""The shard-3 root cause, isolated and pinned platform-independently.

    The failing CI string, verbatim from the Windows runner:

        cat C:\Users\runneradmin\AppData\Local\Temp\pytest-of-runneradmin\pytest-0
            \popen-gw1\test_a_tool_bash_read_of_a_pod0\pod-os-home\.aws\sso\cache
            \<64 hex>.token.json

    On that runner ``is_sensitive_path`` on the same string PASSED while
    ``is_sensitive_bash_command`` returned ``None`` -- so the target set and the
    path matcher were both fine, and the defect was in what the BASH leg handed
    them. ``_shell_tokens`` runs ``shlex.split(posix=True)``, which consumes every
    ``\`` as an escape: the candidate arrived as
    ``C:UsersrunneradminAppDataLocalTemp...token.json`` with no separators at all,
    which can never match a target.

    This module already recorded the identical mangling for ``$HOME`` expansion and
    fixed it by expanding after tokenization. A path typed LITERALLY reaches the
    same de-escaper from a different direction, so that fix could not help it.

    Runs on every platform because it tests the TOKENIZER, not the filesystem: the
    strings are fixtures, nothing is resolved, and no Windows path semantics are
    emulated. That is deliberate -- emulating ``ntpath`` here would test the
    emulation, and the comparison it would exercise is already proven working on
    the real runner.
    """

    CI_OS_HOME = (
        r"C:\Users\runneradmin\AppData\Local\Temp\pytest-of-runneradmin"
        r"\pytest-0\popen-gw1\test_a_tool_bash_read_of_a_pod0\pod-os-home"
    )

    @property
    def ci_grant(self) -> str:
        return self.CI_OS_HOME + r"\.aws\sso\cache" + "\\" + ("a" * 64) + ".token.json"

    def test_shlex_tokenization_destroys_the_path(self) -> None:
        """RED-FIRST, pinned: this is what the matcher used to receive."""
        tokens = security._shell_tokens(f"cat {self.ci_grant}")

        assert tokens[0] == "cat"
        mangled = tokens[1]
        # Every separator consumed as an escape: the segments are run together, so
        # no target can match. (".aws" survives as TEXT -- it is the boundary that
        # is gone, which is what the comparison needs.)
        assert "\\" not in mangled, "shlex kept the separators; premise no longer holds"
        assert mangled != self.ci_grant
        assert "pod-os-home.aws" in mangled, f"expected run-together segments, got {mangled!r}"

    def test_the_windows_native_reading_recovers_the_intact_path(self) -> None:
        """The fix: the un-de-escaped spelling is added as its own reading."""
        recovered = security._windows_native_path_tokens(f"cat {self.ci_grant}")

        assert recovered == [self.ci_grant]

    def test_the_bash_leg_now_hands_the_matcher_the_intact_spelling(self, monkeypatch) -> None:
        """End to end, without emulating Windows path comparison.

        ``is_sensitive_path`` is the leg that decides fenced identity, and it
        already works on the real runner. What was broken is WHAT it was given, so
        that is what this asserts: the intact Windows spelling now reaches it.
        Recording rather than emulating keeps the test honest about which half of
        the pipeline it proves.
        """
        seen: list[str] = []

        def _recorder(path: str, base_dir: str | None = None) -> bool:
            seen.append(path)
            return False

        monkeypatch.setattr(security, "is_sensitive_path", _recorder)
        # The ONE platform predicate a POSIX host cannot satisfy: ``_is_path_like``
        # recognises a drive-letter token only when its drive matches an anchor root
        # (``Path.home()`` / ``KIROCREW_HOME`` / ``KIROCREW_OS_HOME``), and on Linux
        # no root has a drive. Supplying it drives the Windows branch without
        # emulating path comparison -- everything else in the leg is real.
        monkeypatch.setattr(security, "_is_path_like", lambda _token: True)
        security._check_sensitive_via_normalizer(f"cat {self.ci_grant}")

        assert self.ci_grant in seen, f"intact spelling never offered; saw {seen}"

    def test_the_pod_os_home_is_an_anchor_root_for_drive_recognition(self, monkeypatch) -> None:
        """``_win_anchor_roots`` must carry the pod home, not just the crew home.

        Its own docstring states the rule: a root whose keystone leaves are
        re-anchored must have its DRIVE recognised, or a backslash token on that
        drive never reaches ``is_sensitive_path``. This PR made
        ``KIROCREW_OS_HOME`` such a root and it was missing from the list -- benign
        while the pod home sits on the user's drive, a hole the moment it does not.
        """
        monkeypatch.setenv("KIROCREW_OS_HOME", r"D:\pods\demo\os-home")

        assert r"D:\pods\demo\os-home" in security._win_anchor_roots()

    def test_a_quoted_windows_path_is_recovered_too(self) -> None:
        """Quotes are stripped from the ends, so the quoted spelling still yields it."""
        assert security._windows_native_path_tokens(f'cat "{self.ci_grant}"') == [self.ci_grant]

    def test_a_unc_path_is_recovered(self) -> None:
        r"""``\\host\share\...`` has its own anchor and the same de-escaping problem."""
        unc = r"\\fileserver\profiles\runneradmin\.aws\credentials"

        assert security._windows_native_path_tokens(f"cat {unc}") == [unc]

    def test_a_posix_command_gains_no_extra_readings(self) -> None:
        """Monotone, but not indiscriminate: nothing Windows-shaped, nothing added."""
        assert security._windows_native_path_tokens("cat /home/me/.aws/credentials") == []
        assert security._windows_native_path_tokens("grep -r pattern src/") == []
        # An option token carrying a colon must not be read as a drive path.
        assert security._windows_native_path_tokens("ssh -o StrictHostKeyChecking=no h") == []
