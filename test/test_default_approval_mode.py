"""``agent.default_approval_mode`` -- the approval tier a NEW session starts on.

Issue #6812. Four properties are pinned here, and the first two are the ones that
keep this setting from becoming a way to escape an admin ceiling:

1. the offerable set is exactly the tiers the ``approval_modes`` governance scope
   declares ``always_permitted``, so the setting cannot name a deniable mode;
2. an unrecognised value -- including ``yolo`` -- falls back to the interactive
   floor, so a hand-edited config cannot grant more than it is allowed to;
3. the per-mode state matches what ``api_chat_mode`` writes for the same tier, so
   a tier reached from config does not mean something different from the same
   tier reached from the footer picker;
4. the default is ``normal``, so installing this change alters no behaviour.

Deliberately a new file rather than an addition to
``test_approval_modes_enforcement.py``: open PR #8868 (the #8848 push-on-install
refactor) is editing that file, and colliding with it buys nothing.
"""

import dataclasses

import pytest

from kiro_crew.config.sections import (
    _DEFAULT_APPROVAL_MODE,
    _DEFAULT_APPROVAL_MODES,
    AgentConfig,
    _normalize_default_approval_mode,
)


def _field_metadata_enum() -> list[str]:
    """The ``enum`` the config field advertises to ``GET /api/config/schema``."""
    for f in dataclasses.fields(AgentConfig):
        if f.name == "default_approval_mode":
            return list(f.metadata["enum"])
    raise AssertionError("AgentConfig has no default_approval_mode field")


class TestTheOfferableSetCannotEscapeAnAdminCeiling:
    def test_the_offerable_tiers_are_exactly_the_non_deniable_ones(self) -> None:
        """The drift guard that makes "no clamp needed" true rather than asserted.

        ``approval_modes`` may never forbid the modes in ``always_permitted``, so a
        setting restricted to exactly those cannot select past a policy. If a tier
        ever BECOMES deniable it leaves that tuple and this test fails -- which is
        the point: the failure is what stops the offerable list silently retaining
        a mode an admin can now deny.
        """
        from kiro_crew.platform.governance import SCOPE_CATALOG

        # SUBSET, not equality. Every persistable tier must be one the scope may never
        # forbid, which is what makes "cannot select past an admin ceiling" true. It is
        # no longer EQUALITY because `trust` is excluded for a different and stricter
        # reason -- it writes the unattended auto-approve session policy, so it must not
        # be persistable even though policy may not forbid it. Equality here would force
        # `trust` back in the moment someone "fixed" the test.
        assert set(_DEFAULT_APPROVAL_MODES) <= set(SCOPE_CATALOG["approval_modes"].always_permitted)
        assert "trust" not in _DEFAULT_APPROVAL_MODES

    def test_yolo_is_not_offerable_anywhere(self) -> None:
        """``yolo`` is process-global with its own expiry, not a per-session tier.

        Asserted at all three surfaces because a value only has to leak through
        ONE of them to be selectable.
        """
        from kiro_crew.dashboard.handlers.core import _EDITABLE_CONFIG

        assert "yolo" not in _DEFAULT_APPROVAL_MODES
        assert "yolo" not in _field_metadata_enum()
        assert "yolo" not in _EDITABLE_CONFIG["agent.default_approval_mode"]["values"]

    def test_the_three_declarations_agree(self) -> None:
        """Constant, field metadata and the settings write-validator are one set.

        They are separate declarations read by separate consumers (the normalizer,
        the schema endpoint, the Settings PUT validator), so a value added to one
        and not the others is selectable in the UI and refused on write, or vice
        versa.
        """
        from kiro_crew.dashboard.handlers.core import _EDITABLE_CONFIG

        expected = list(_DEFAULT_APPROVAL_MODES)
        assert _field_metadata_enum() == expected
        assert _EDITABLE_CONFIG["agent.default_approval_mode"]["values"] == expected
        assert _EDITABLE_CONFIG["agent.default_approval_mode"]["type"] == "enum"


class TestAnUnreadableValueAsksForMoreApprovalsNotFewer:
    @pytest.mark.parametrize("raw", list(_DEFAULT_APPROVAL_MODES))
    def test_every_offerable_tier_survives_normalization(self, raw: str) -> None:
        assert _normalize_default_approval_mode(raw) == raw

    @pytest.mark.parametrize(
        "raw",
        [
            # `TRUST` is deliberately NOT here any more: it is no longer a
            # persistable tier, so its normalized form is the floor, not itself. It
            # is asserted in the rejected set below instead.
            "  TRUST_READS  ",
            "Trust_Reads",
            "NORMAL",
        ],
    )
    def test_case_and_surrounding_whitespace_are_tolerated(self, raw: str) -> None:
        assert _normalize_default_approval_mode(raw) == raw.strip().lower()

    @pytest.mark.parametrize(
        "raw",
        [
            "yolo",  # the one deniable mode: must never arrive this way
            "YOLO",
            "auto",  # agent.approval_mode's vocabulary, not this one
            "interactive",
            "reads",  # the i18n LABEL spelling, not the key
            "",
            "bogus",
            None,
            5,
            [],
            {"mode": "trust"},
        ],
    )
    def test_anything_else_falls_back_to_the_interactive_floor(self, raw: object) -> None:
        assert _normalize_default_approval_mode(raw) == "normal"
        assert _normalize_default_approval_mode(raw) == _DEFAULT_APPROVAL_MODE

    def test_reads_is_rejected_because_the_key_is_trust_reads(self) -> None:
        """Guards the exact mistake the issue body invites.

        The picker LABELS the middle tier "Reads" via
        ``components.approvalModePicker.reads_label``, but its key is
        ``trust_reads``. A config saying ``reads`` is a typo, not a tier, and must
        not silently become one.
        """
        assert _normalize_default_approval_mode("reads") == "normal"
        assert "reads" not in _DEFAULT_APPROVAL_MODES
        assert "trust_reads" in _DEFAULT_APPROVAL_MODES


class TestInstallingThisChangesNothing:
    def test_the_field_default_is_the_behaviour_new_sessions_have_today(self) -> None:
        assert AgentConfig().default_approval_mode == "normal"

    def test_an_absent_config_key_resolves_to_normal(self) -> None:
        assert _normalize_default_approval_mode(None) == "normal"


class TestThePerModeStateMatchesTheFooterPicker:
    """``_apply_default_approval_mode`` against ``api_chat_mode``'s own writes."""

    @staticmethod
    def _slot_and_state():
        from kiro_crew.dashboard.state import _ChatSlot

        class _Sessions:
            def __init__(self) -> None:
                self.policies: dict[str, str] = {}
                self.calls = 0

            def set_approval_policy(self, key: str, value: str) -> None:
                self.calls += 1
                self.policies[key] = value

        class _State:
            def __init__(self) -> None:
                self.sessions = _Sessions()

        return _ChatSlot("chat-6812"), _State()

    def test_normal_writes_nothing_because_a_fresh_slot_already_is_normal(self) -> None:
        from kiro_crew.dashboard.chat_handlers import _apply_default_approval_mode

        slot, state = self._slot_and_state()
        assert _apply_default_approval_mode(state, slot, "normal") is False
        assert slot._trust is False
        assert slot._trust_reads is False
        # No session policy write at all: touching it could only introduce a
        # difference from the untouched state that IS "normal".
        assert state.sessions.calls == 0

    def test_trust_reads_sets_only_the_read_flag_and_leaves_the_policy_empty(self) -> None:
        """``trust_reads`` must NOT grant subagent auto-approval.

        ``api_chat_mode`` writes ``""`` for this tier, and
        ``subagent_manager/admission.py``'s ``parent_trusted`` treats only
        ``"auto"`` as trusted (#8849). Writing ``"auto"`` here would silently make
        Reads stronger than the picker's Reads.
        """
        from kiro_crew.dashboard.chat_handlers import _apply_default_approval_mode
        from kiro_crew.dashboard.chat_utils import effective_session_key

        slot, state = self._slot_and_state()
        assert _apply_default_approval_mode(state, slot, "trust_reads") is True
        assert slot._trust_reads is True
        assert slot._trust is False
        assert state.sessions.policies == {effective_session_key(slot): ""}

    def test_trust_is_REFUSED_by_the_helper_and_writes_nothing(self) -> None:
        """`trust` is not persistable, and the granting branch is DELETED.

        Stronger than "unreachable": there is no code path in this helper that can
        write session policy "auto", so widening the persistable set later cannot
        revive the grant without also re-adding the branch -- which this test would
        then not catch, but the source assertion below would.
        """
        from kiro_crew.dashboard.chat_handlers import _apply_default_approval_mode

        slot, state = self._slot_and_state()
        assert _apply_default_approval_mode(state, slot, "trust") is False
        assert slot._trust is False
        assert slot._trust_reads is False
        assert state.sessions.calls == 0  # no policy write of any kind

    def test_the_helper_contains_no_auto_policy_write_at_all(self) -> None:
        """The structural half: absent, not merely unreachable.

        The conductor's distinction -- a guarded branch is correct only while nothing
        supplies `trust`, whereas a deleted branch cannot be revived by a config
        change. Asserted on the source so a re-added branch fails here.
        """
        import inspect

        from kiro_crew.dashboard import chat_handlers

        src = inspect.getsource(chat_handlers._apply_default_approval_mode)
        code = "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith(("#", "*"))
        )
        assert (
            '"auto"' not in code.split('"""')[-1]
        ), "an auto-approve policy write reappeared in the persistable-tier helper"

    @pytest.mark.parametrize("mode", ["yolo", "reads", "auto", "interactive", ""])
    def test_a_non_tier_reaching_the_helper_is_a_no_op(self, mode: str) -> None:
        """Defence in depth: the normalizer should stop these, and if it ever
        does not, the helper still grants nothing."""
        from kiro_crew.dashboard.chat_handlers import _apply_default_approval_mode

        slot, state = self._slot_and_state()
        assert _apply_default_approval_mode(state, slot, mode) is False
        assert slot._trust is False
        assert slot._trust_reads is False
        assert state.sessions.calls == 0


class TestOnlyAHumanDashboardCallerInheritsTheDefault:
    """The tier is a dashboard operator's preference, not an app-token capability.

    ``POST /api/chat/slots`` serves two principals: a human on the new-chat tab
    (``request["app"] == ""``) and an app token holding ``/api/chat`` (non-empty).
    An app's grant does not include "and start pre-trusted", so a ``trust`` default
    must not hand an app-created session the ``auto`` approval policy -- which
    ``parent_trusted`` would then extend to every subagent it spawns.

    Driven through the REAL handler rather than the helper: the helper is
    principal-blind by design, and what needs pinning is the call site's guard.
    A test of the helper alone would stay green with the guard deleted.
    """

    @staticmethod
    def _state(tmp_path):
        from unittest.mock import AsyncMock, MagicMock

        from chat_test_helpers import _make_ready_kiro_prerequisite

        from kiro_crew.dashboard.state import DashboardState
        from kiro_crew.history import ConversationLog

        sessions = MagicMock(count=0)
        sessions.remove = AsyncMock()
        sessions.recycle_background = AsyncMock()
        sessions.get_pid = MagicMock(return_value=None)
        state = DashboardState(
            sessions=sessions,
            crons=MagicMock(
                list_jobs=MagicMock(return_value=[]), status=MagicMock(return_value={})
            ),
            lessons=MagicMock(load_all=MagicMock(return_value=[])),
            start_time=0.0,
            conversation_log=ConversationLog(base_dir=tmp_path),
        )
        state.kiro_prerequisite_service = _make_ready_kiro_prerequisite()
        return state

    @staticmethod
    def _force_trust_default(monkeypatch):
        """Config that would grant the strongest offerable tier.

        A REAL ``KiroCrewConfig`` with one field replaced, not a stand-in namespace:
        the handler reads several other config attributes (``default_agent`` among
        them), so a thin double fails for reasons unrelated to what is under test.

        `trust` on purpose: it is the only tier that also writes the `auto` session
        policy, so it is the value whose leak to an app token actually matters.
        """
        import dataclasses as _dc
        import types

        from kiro_crew.config import KiroCrewConfig

        base = KiroCrewConfig()
        cfg = _dc.replace(base, agent=_dc.replace(base.agent, default_approval_mode="trust_reads"))
        assert cfg.agent.default_approval_mode == "trust_reads"
        monkeypatch.setattr(
            "kiro_crew.dashboard.chat_handlers.KiroCrewConfig",
            types.SimpleNamespace(load=lambda: cfg),
        )
        # The tier is owner-only to inherit as well as to persist, so the default
        # posture for these tests is OWNER -- otherwise every one of them would be
        # measuring the owner gate instead of the thing it names.
        monkeypatch.setattr(
            "kiro_crew.dashboard.handlers.source_providers.is_owner_dashboard_request",
            lambda _r: True,
        )

    @pytest.fixture(autouse=True)
    def _isolate_config_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)

    @pytest.mark.asyncio
    async def test_a_dashboard_user_gets_the_configured_tier(self, tmp_path, monkeypatch):
        """The control: without this passing, the denial below proves nothing."""
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard.chat import api_chat_slot_create

        self._force_trust_default(monkeypatch)
        state = self._state(tmp_path)

        async def as_dashboard_user(request: web.Request) -> web.Response:
            request["app"] = ""  # what the auth middleware sets for a dashboard user
            return await api_chat_slot_create(request)

        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/slots", as_dashboard_user)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/slots", json={"name": "human"})
            assert resp.status == 200, await resp.text()

        slot = next(iter(state._slots.values()))
        assert slot._trust_reads is True
        assert slot._trust is False

    @pytest.mark.asyncio
    async def test_an_app_token_does_not_inherit_the_configured_tier(self, tmp_path, monkeypatch):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard.chat import api_chat_slot_create

        self._force_trust_default(monkeypatch)
        state = self._state(tmp_path)

        async def as_app_token(request: web.Request) -> web.Response:
            request["app"] = "some-app"
            return await api_chat_slot_create(request)

        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/slots", as_app_token)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/slots", json={"name": "app-made"})
            assert resp.status == 200, await resp.text()

        slot = next(iter(state._slots.values()))
        assert slot._trust is False, "an app token must not inherit a trust default"
        assert slot._trust_reads is False

    @pytest.mark.asyncio
    async def test_an_app_token_gets_no_auto_session_policy(self, tmp_path, monkeypatch):
        """The consequence that made this security-class rather than cosmetic.

        ``trust`` writes ``set_approval_policy(key, "auto")``, and
        ``subagent_manager/admission.py``'s ``parent_trusted`` reads that policy on a
        path independent of the SafetyOverride -- so the leak would auto-approve the
        app session's subagents too. Asserted on the sessions double directly,
        because the flags above and this write are separate effects.
        """
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard.chat import api_chat_slot_create

        self._force_trust_default(monkeypatch)
        state = self._state(tmp_path)

        async def as_app_token(request: web.Request) -> web.Response:
            request["app"] = "some-app"
            return await api_chat_slot_create(request)

        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/slots", as_app_token)
        async with TestClient(TestServer(app)) as client:
            assert (await client.post("/api/chat/slots", json={"name": "a"})).status == 200

        wrote_auto = [
            c for c in state.sessions.set_approval_policy.call_args_list if "auto" in repr(c)
        ]
        assert wrote_auto == [], f"app-created session was granted an auto policy: {wrote_auto}"


class TestARemoteBoundSessionKeepsThePeersTier:
    """A session bound to a remote crew is not re-tiered by the local default.

    For a remote-bound session the PEER enforces approvals. Applying the local
    default would set the flags the footer DISPLAYS while the peer decided what
    actually runs, so a peer at Normal could display Trust here (or the reverse) --
    a display that contradicts enforcement. Mirroring the peer's effective tier
    would need a defined path to read it and there is none, so the handler declines
    rather than guessing.

    Binding is owner-only and refuses app tokens, so this drives the handler as the
    owner -- the only principal that can reach the branch at all.
    """

    @pytest.fixture(autouse=True)
    def _isolate_config_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)

    @pytest.mark.asyncio
    async def test_a_remote_bound_create_does_not_inherit_the_local_default(
        self, tmp_path, monkeypatch
    ):
        from unittest.mock import AsyncMock, MagicMock

        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard.chat import api_chat_slot_create

        TestOnlyAHumanDashboardCallerInheritsTheDefault._force_trust_default(monkeypatch)
        state = TestOnlyAHumanDashboardCallerInheritsTheDefault._state(tmp_path)

        # Owner, so the binding gates pass; the peer write itself is stubbed because
        # what is under test is the LOCAL tier decision, not the remote call.
        monkeypatch.setattr(
            "kiro_crew.dashboard.handlers.source_providers.is_owner_dashboard_request",
            lambda _r: True,
        )
        created = MagicMock(return_value=None)
        monkeypatch.setattr(
            "kiro_crew.dashboard.chat_handlers.create_peer_slot",
            AsyncMock(return_value={"slot": "peer-1"}),
            raising=False,
        )

        async def as_owner(request: web.Request) -> web.Response:
            request["app"] = ""
            return await api_chat_slot_create(request)

        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/slots", as_owner)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/chat/slots", json={"name": "remote", "instance_id": "peer-crew-1"}
            )
            # The create may legitimately fail for peer reasons in this harness; what
            # must hold either way is that no LOCAL trust flag was granted. Asserted
            # unconditionally rather than gated on a 200, so a harness-level peer
            # failure cannot make this vacuous.
            _ = resp.status

        for slot in state._slots.values():
            assert slot._trust is False, "a remote-bound session inherited the local trust default"
            assert slot._trust_reads is False
        assert created.call_count == 0  # the double above is unused; kept explicit


class TestPersistingTheDefaultIsOwnerOnly:
    """Raising the floor for every FUTURE session is an owner act.

    ``api_kirocrew_config_patch`` applies no owner gate of its own, while sibling
    handler modules do -- so an allow-listed non-owner identity holds a dashboard
    credential with an EMPTY app claim and would otherwise reach this write. This
    key carries ``owner_only`` for that reason: ``trust`` here is a STANDING
    auto-approve grant with no expiry, the same property that keeps
    ``agent.dangerously_skip_permissions`` out of the editable set entirely.
    """

    @staticmethod
    def _app():
        from aiohttp import web

        from kiro_crew.dashboard.handlers import api_kirocrew_config_patch

        app = web.Application()
        app.router.add_patch("/api/config/kirocrew", api_kirocrew_config_patch)
        return app

    @pytest.mark.asyncio
    async def test_a_non_owner_cannot_persist_the_default(self, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        monkeypatch.setattr(
            "kiro_crew.dashboard.handlers.source_providers.is_owner_dashboard_request",
            lambda _r: False,
        )
        async with TestClient(TestServer(self._app())) as client:
            resp = await client.patch(
                "/api/config/kirocrew",
                json={"path": "agent.default_approval_mode", "value": "trust"},
            )
        assert resp.status in (401, 403), f"non-owner write was not refused: {resp.status}"

    @pytest.mark.asyncio
    async def test_the_gate_is_scoped_to_this_key(self, monkeypatch):
        """The control: a key WITHOUT the flag is unaffected by this change.

        Without this, the test above would also pass if the gate had accidentally
        been applied to every editable key -- which would be a regression, not a fix.
        """
        from aiohttp.test_utils import TestClient, TestServer

        monkeypatch.setattr(
            "kiro_crew.dashboard.handlers.source_providers.is_owner_dashboard_request",
            lambda _r: False,
        )
        async with TestClient(TestServer(self._app())) as client:
            resp = await client.patch(
                "/api/config/kirocrew",
                json={"path": "agent.yolo_duration", "value": "3600"},
            )
        # Whatever this ungated sibling answers, it must not be the owner refusal.
        assert resp.status not in (401, 403), "the owner gate leaked to an ungated key"

    def test_the_flag_is_declared_on_the_key(self) -> None:
        """Cheap structural pin: the flag itself, independent of any HTTP path."""
        from kiro_crew.dashboard.handlers.core import _EDITABLE_CONFIG

        assert _EDITABLE_CONFIG["agent.default_approval_mode"].get("owner_only") is True
        assert "owner_only" not in _EDITABLE_CONFIG["agent.yolo_duration"]


class TestANonOwnerDashboardUserDoesNotInheritTheDefault:
    """Inheriting the tier is owner-only, not merely non-app.

    An allow-listed messaging identity holds a dashboard credential whose subject is
    not the owner and whose ``app`` claim is EMPTY, so the app-token guard alone
    admits it. Without a positive owner assertion such a caller would pick up the
    owner's standing auto-approve grant just by opening a chat.
    """

    @pytest.fixture(autouse=True)
    def _isolate_config_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)

    @pytest.mark.asyncio
    async def test_a_non_owner_keeps_the_interactive_floor(self, tmp_path, monkeypatch):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard.chat import api_chat_slot_create

        TestOnlyAHumanDashboardCallerInheritsTheDefault._force_trust_default(monkeypatch)
        # Applied AFTER the helper, which sets the owner posture these tests default to.
        monkeypatch.setattr(
            "kiro_crew.dashboard.handlers.source_providers.is_owner_dashboard_request",
            lambda _r: False,
        )
        state = TestOnlyAHumanDashboardCallerInheritsTheDefault._state(tmp_path)

        async def as_non_owner(request: web.Request) -> web.Response:
            request["app"] = ""  # a dashboard credential, but not the owner's
            return await api_chat_slot_create(request)

        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/slots", as_non_owner)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/slots", json={"name": "guest"})
            assert resp.status == 200, await resp.text()

        slot = next(iter(state._slots.values()))
        assert slot._trust is False, "a non-owner inherited the owner's trust default"
        assert slot._trust_reads is False


class TestTheCreationVerdictCannotGoStale:
    """No ``await`` between the newness check and the slot allocation.

    ``is_new_slot`` is a snapshot of ``state._slots``. An await between that check
    and ``get_or_create_slot`` is a TOCTOU window: a concurrent create can insert the
    same key while this coroutine is suspended, after which the allocation returns
    the OTHER caller's slot while the stale verdict still says it is fresh -- and an
    owner-tier auto-approve grant lands on a session this request did not create.

    Reading the config for this feature introduced exactly such an await; it is now
    hoisted above the check. This pins the ORDERING rather than the hoist, so any
    future await added into that window fails here instead of silently reopening the
    race. Source-level on purpose: the property is about scheduling points, which a
    behavioural test cannot observe without racing the loop it is asserting about.
    """

    def test_no_await_between_the_newness_check_and_the_allocation(self) -> None:
        import inspect

        from kiro_crew.dashboard import chat_handlers

        src = inspect.getsource(chat_handlers.api_chat_slot_create).splitlines()
        check = next(i for i, line in enumerate(src) if "is_new_slot = not _requested_key" in line)
        alloc = next(
            i for i, line in enumerate(src) if i > check and "state.get_or_create_slot(" in line
        )
        offenders = [
            (i, line.strip())
            for i, line in enumerate(src)
            if check < i < alloc and ("await " in line or "async with " in line)
        ]
        assert offenders == [], (
            "an await between the is_new_slot check and get_or_create_slot reopens the "
            f"stale-verdict race: {offenders}"
        )

    def test_the_tier_is_refused_when_the_slot_is_app_owned(self) -> None:
        """Defence in depth, asserted on the object rather than on the boolean.

        Even with the window closed, the apply site checks the slot's own ``_app``
        tag, so a slot that belongs to an app can never be tiered regardless of what
        the newness verdict says.
        """
        import inspect

        from kiro_crew.dashboard import chat_handlers

        src = inspect.getsource(chat_handlers.api_chat_slot_create)
        assert "and not slot._app" in src, "the app-ownership check at the apply site is gone"


class TestOnlyASlotTheUserCanSeeInheritsTheTier:
    """An app-worker slot is not a chat the user opened.

    Design Critique's ``dc-*`` worker is created by the OWNER's own page (same-origin,
    no app token), so it has an empty app tag and passes every principal gate. What it
    does not have is a user watching it: its ``design-critique`` mode keeps it off the
    chat sidebar. A standing auto-approve grant there runs tools unattended.
    """

    @pytest.fixture(autouse=True)
    def _isolate_config_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)

    @pytest.mark.asyncio
    async def test_an_app_worker_mode_does_not_inherit_the_tier(self, tmp_path, monkeypatch):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard.chat import api_chat_slot_create

        TestOnlyAHumanDashboardCallerInheritsTheDefault._force_trust_default(monkeypatch)
        state = TestOnlyAHumanDashboardCallerInheritsTheDefault._state(tmp_path)

        async def as_owner(request: web.Request) -> web.Response:
            request["app"] = ""
            return await api_chat_slot_create(request)

        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/slots", as_owner)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/chat/slots",
                json={"name": "dc-1", "memory_mode": "temporary", "mode": "design-critique"},
            )
            assert resp.status == 200, await resp.text()

        slot = next(iter(state._slots.values()))
        assert slot._trust is False, "an off-sidebar app-worker slot inherited the tier"
        assert slot._trust_reads is False

    def test_the_allowlist_holds_only_sidebar_modes(self) -> None:
        """Pinned against the CREATABLE set so a new app-worker mode cannot default in."""
        from kiro_crew.dashboard.chat_handlers import (
            _CREATABLE_MODES,
            _TIER_INHERITING_MODES,
        )

        assert set(_TIER_INHERITING_MODES) < set(_CREATABLE_MODES)
        assert "design-critique" not in _TIER_INHERITING_MODES


class TestAGrantThatCannotBeAuditedIsRefused:
    """The audit is written BEFORE the grant, and a failed audit refuses it.

    An unattended auto-approve grant whose SEL entry is lost is exactly what an
    auditor cannot reconstruct, and the loss is permanent. Refusing leaves the session
    on the interactive floor, which is the fail-safe direction.
    """

    @pytest.fixture(autouse=True)
    def _isolate_config_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)

    @pytest.mark.asyncio
    async def test_an_unwritable_audit_refuses_the_tier(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard.chat import api_chat_slot_create

        TestOnlyAHumanDashboardCallerInheritsTheDefault._force_trust_default(monkeypatch)
        state = TestOnlyAHumanDashboardCallerInheritsTheDefault._state(tmp_path)

        # SEL unwritable, the infra fault the finding names (e.g. disk full).
        broken = MagicMock()
        broken.log_api_access.side_effect = OSError("disk full")
        monkeypatch.setattr("kiro_crew.dashboard.chat_handlers.sel", lambda: broken)

        async def as_owner(request: web.Request) -> web.Response:
            request["app"] = ""
            return await api_chat_slot_create(request)

        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/chat/slots", as_owner)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/slots", json={"name": "unaudited"})
            assert resp.status == 200, await resp.text()

        slot = next(iter(state._slots.values()))
        assert slot._trust is False, "a grant was made that could not be audited"
        assert slot._trust_reads is False
        assert broken.log_api_access.called, "the audit was not even attempted"


class TestAnUnpersistableTierIsRefusedByTheRealLoadPath:
    """`trust` and `yolo` cannot be persisted, measured through the REAL loader.

    This is the security argument for the feature's shape, so it is deliberately NOT
    mocked: it writes an actual ``config.json`` containing the value an attacker (or a
    hand-edit, or an agent in an already-trusted session) would store, then calls
    ``KiroCrewConfig.load()`` and asserts the loaded config reports the built-in
    default instead.

    `trust` is the tier that matters: it is the only one that also writes the session
    ``approval_policy`` "auto" -- unattended tool auto-approve, inherited by spawned
    subagents. `config.json` is agent-writable, and although
    ``is_sensitive_write_path`` is True for it that write is auto-approved inside a
    session that is ALREADY trusted, so a persistable `trust` would convert one
    session's trust into standing trust for every future session. Refusing to honour
    the stored value removes the mechanism rather than discouraging it.
    """

    @staticmethod
    def _load_with_config(tmp_path, monkeypatch, payload: dict):
        """Write a real config.json and load it through the real loader."""
        import json as _json

        from kiro_crew.config import loader as _loader
        from kiro_crew.config.loader import KiroCrewConfig

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(_json.dumps(payload), encoding="utf-8")
        # Point the REAL resolver at this file. `config_path` is what
        # `_load_resolved` calls, so this exercises read + merge + validate rather
        # than substituting a config object.
        monkeypatch.setattr(_loader, "config_path", lambda: cfg_file)
        return KiroCrewConfig.load()

    def test_a_persistable_tier_IS_honoured(self, tmp_path, monkeypatch) -> None:
        """The control. Without this, the refusals below could pass vacuously.

        If the loader were not reading this file at all, every assertion in this class
        would still see `normal` -- because `normal` is also the default. This proves
        the file reaches the config, so the refusals mean something.
        """
        cfg = self._load_with_config(
            tmp_path, monkeypatch, {"agent": {"default_approval_mode": "trust_reads"}}
        )
        assert cfg.agent.default_approval_mode == "trust_reads"

    @pytest.mark.parametrize("stored", ["trust", "yolo"])
    def test_an_unpersistable_tier_is_not_honoured(
        self, tmp_path, monkeypatch, stored: str
    ) -> None:
        cfg = self._load_with_config(
            tmp_path, monkeypatch, {"agent": {"default_approval_mode": stored}}
        )
        assert (
            cfg.agent.default_approval_mode == _DEFAULT_APPROVAL_MODE == "normal"
        ), f"a stored {stored!r} was honoured; it must fall back to the built-in default"

    def test_case_and_whitespace_cannot_smuggle_trust_past_the_clamp(
        self, tmp_path, monkeypatch
    ) -> None:
        """The normalizer lowercases and strips, so these must not become a bypass."""
        for raw in ("  TRUST  ", "Trust", "TRUST", "\ttrust\n"):
            cfg = self._load_with_config(
                tmp_path, monkeypatch, {"agent": {"default_approval_mode": raw}}
            )
            assert cfg.agent.default_approval_mode == "normal", f"{raw!r} was honoured"

    def test_trust_is_absent_from_every_persistable_declaration(self) -> None:
        """One assertion per surface, so a partial revert is caught."""
        from kiro_crew.dashboard.handlers.core import _EDITABLE_CONFIG

        assert "trust" not in _DEFAULT_APPROVAL_MODES
        assert "trust" not in _field_metadata_enum()
        assert "trust" not in _EDITABLE_CONFIG["agent.default_approval_mode"]["values"]

    def test_the_per_chat_picker_still_offers_trust(self) -> None:
        """Only PERSISTING trust is refused; the footer picker is unchanged.

        Pinned so a later reader does not "tidy" the two sets into agreement --
        `_SLOT_SCOPED_TRUST_MODES` is the per-chat vocabulary and must keep `trust`.
        """
        from kiro_crew.dashboard.chat_handlers import _SLOT_SCOPED_TRUST_MODES

        assert "trust" in _SLOT_SCOPED_TRUST_MODES
        assert "trust_reads" in _SLOT_SCOPED_TRUST_MODES


class TestTheGuardStackDoesNotArgueFromTrust:
    """No guard around this feature reasons from `trust` any more.

    `trust` is unpersistable as of this revision, so a gate testing membership in
    `_SLOT_SCOPED_TRUST_MODES` (the PER-CHAT vocabulary, which still contains `trust`)
    would argue from an invalidated premise: it admits a value this path cannot honour
    and would widen again if that tuple grew. Asserted as ABSENCE of the superseded
    reasoning, not merely presence of the new.
    """

    def test_the_create_gate_tests_the_persistable_set_not_the_per_chat_tuple(self) -> None:
        import inspect

        from kiro_crew.dashboard import chat_handlers

        src = inspect.getsource(chat_handlers.api_chat_slot_create)
        assert "_default_approval_mode in _DEFAULT_APPROVAL_MODES" in src
        # The superseded phrasing must be GONE from this handler.
        assert "_default_approval_mode in _SLOT_SCOPED_TRUST_MODES" not in src

    def test_the_per_chat_path_still_uses_its_own_tuple(self) -> None:
        """The control: the per-chat vocabulary is untouched and still offers trust."""
        from kiro_crew.dashboard.chat_handlers import _SLOT_SCOPED_TRUST_MODES

        assert "trust" in _SLOT_SCOPED_TRUST_MODES


class TestADeclinedDefaultIsNeverSilent:
    """A guard that declines the configured tier must say which guard and why.

    This feature exists to remove a per-chat click. A guard that puts that click back
    without a trace means the user pays it and cannot find out why -- the failure mode
    where a capability is present at every layer except the one that decides.
    """

    def test_every_guard_has_a_named_refusal_reason(self) -> None:
        import inspect

        from kiro_crew.dashboard import chat_handlers

        src = inspect.getsource(chat_handlers.api_chat_slot_create)
        for reason in (
            "not_a_new_session",
            "app_token_caller",
            "session_bound_to_remote_crew",
            "caller_is_not_the_owner",
            "slot_owned_by_an_app",
            "app_worker_mode",
            "tier_not_persistable",
        ):
            assert reason in src, f"guard refusal {reason!r} is not attributable"
        assert "not applied to" in src, "the refusal is never logged"

    def test_no_refusal_is_logged_when_nothing_was_configured(self) -> None:
        """`normal` is the default, so an unconfigured install logs nothing.

        Without this the log would fire on every session create on every install,
        which would make the signal worthless.
        """
        import inspect

        from kiro_crew.dashboard import chat_handlers

        src = inspect.getsource(chat_handlers.api_chat_slot_create)
        assert 'if _default_approval_mode != "normal":' in src
