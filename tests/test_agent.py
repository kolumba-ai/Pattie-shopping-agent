"""Tests for the agent loop, the Telegram bot and the app wiring, with fakes (no network).

    .venv/bin/python -m pytest tests/test_agent.py
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pattie.agent import TOOLS, Agent  # noqa: E402
from pattie.app import AgentDelivery, handle_message  # noqa: E402
from pattie.bridge import DeliveryError  # noqa: E402
from pattie.telegram import Owner, TelegramBot, TelegramError  # noqa: E402


# --- fakes ----------------------------------------------------------------------

class Block(SimpleNamespace):
    def model_dump(self, exclude_none=False):
        return {k: v for k, v in vars(self).items() if not (exclude_none and v is None)}


def text(t):
    return Block(type="text", text=t)


def tool_use(id_, name, args):
    return Block(type="tool_use", id=id_, name=name, input=args)


class FakeClaude:
    """Returns scripted responses and records each request."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs["messages"], default=str)))
        content, stop = self.responses.pop(0)
        return SimpleNamespace(content=content, stop_reason=stop)


class FakeTools:
    def __init__(self):
        self.calls = []

    def find_offers(self, **kw):
        self.calls.append(("find_offers", kw))
        return {"mode": "one", "picks": [{"asin": "TESTPB0001", "title": "Anker Power Bank", "price": 29.99,
                                          "image": "https://example.com/pb.jpg", "sponsored": False}]}

    def checkout(self, **kw):
        self.calls.append(("checkout", kw))
        return {"status": "cart_link", "cart_link": "https://www.amazon.com/gp/aws/cart/add.html?ASIN.1=TESTPB0001"}

    def card_status(self, merchant=None):
        raise RuntimeError("ledger unavailable")

    def remember(self, note):
        self.calls.append(("remember", note))
        return {"saved": note}


class FakeSender:
    def __init__(self):
        self.photos, self.texts = [], []

    def photo(self, chat_id, url, caption=""):
        self.photos.append((chat_id, url, caption))

    def text(self, chat_id, t):
        self.texts.append((chat_id, t))

    def typing(self, chat_id):
        pass


def db():
    return tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False).name


# --- agent ----------------------------------------------------------------------

def test_tool_round_trip_and_history():
    claude = FakeClaude([
        ([tool_use("t1", "find_offers", {"item": "power bank", "search_query": "Anker power bank",
                                         "category": "accessories"})], "tool_use"),
        ([tool_use("t2", "send_photo", {"url": "https://example.com/pb.jpg", "caption": "Anker, $29.99"})], "tool_use"),
        ([text("Here's the Anker power bank for $29.99. Want it?")], "end_turn"),
    ])
    tools, sender = FakeTools(), FakeSender()
    agent = Agent(tools, db(), send=sender, client=claude)
    reply = agent.chat("42", "Find me a power bank")
    assert reply.startswith("Here's the Anker power bank")
    assert tools.calls[0][0] == "find_offers" and sender.photos == [("42", "https://example.com/pb.jpg", "Anker, $29.99")]
    # the tool result went back as one user message with the matching id
    second = claude.requests[1]
    assert second[-1]["role"] == "user" and second[-1]["content"][0]["tool_use_id"] == "t1"
    assert json.loads(second[-1]["content"][0]["content"])["picks"][0]["asin"] == "TESTPB0001"
    # the next message continues the same conversation
    claude.responses.append(([text("Done.")], "end_turn"))
    agent.chat("42", "yes")
    assert claude.requests[-1][0]["content"] == "Find me a power bank"


def test_tool_errors_go_back_to_claude():
    claude = FakeClaude([([tool_use("t1", "card_status", {})], "tool_use"),
                         ([tool_use("t2", "launch_rocket", {})], "tool_use"),
                         ([text("I couldn't read the budget right now.")], "end_turn")])
    agent = Agent(FakeTools(), db(), send=FakeSender(), client=claude)
    assert agent.chat("42", "How much have I spent?").startswith("I couldn't")
    results = [m for m in claude.requests[-1] if m["role"] == "user" and isinstance(m["content"], list)]
    assert results[0]["content"][0]["is_error"] and "ledger unavailable" in results[0]["content"][0]["content"]
    assert results[1]["content"][0]["is_error"] and "unknown tool" in results[1]["content"][0]["content"]


def test_refusal_and_reset():
    claude = FakeClaude([([], "refusal")])
    agent = Agent(FakeTools(), db(), send=FakeSender(), client=claude)
    assert "can't help" in agent.chat("42", "something")
    agent.reset("42")
    assert agent._load("42") == []


def test_calendar_event_is_labeled():
    claude = FakeClaude([([text("Hackathon tomorrow! Want a power bank?")], "end_turn")])
    agent = Agent(FakeTools(), db(), send=FakeSender(), client=claude)
    agent.event("42", "calendar_event", {"event": {"title": "Hackathon SF"}})
    assert claude.requests[0][0]["content"].startswith("[Calendar event] ")
    agent.client = FakeClaude([([text("Breakfast?")], "end_turn")])
    agent.event("42", "food_nudge", {"nudge": {"meal": "breakfast"}})
    assert agent.client.requests[0][-1]["content"].startswith("[Food nudge] ")


def test_tool_definitions_are_valid():
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names))
    for t in TOOLS:
        assert t["input_schema"]["type"] == "object" and t["description"]
        assert set(t["input_schema"].get("required", [])) <= set(t["input_schema"]["properties"])


class FakeAmplitude:
    def __init__(self):
        self.events = []

    def track(self, event):
        self.events.append((event.event_type, event.event_properties))


def test_traces_go_to_amplitude_and_findings_to_memory():
    import time
    claude = FakeClaude([
        ([tool_use("t1", "find_offers", {"item": "power bank", "search_query": "Anker power bank",
                                         "category": "accessories"})], "tool_use"),
        ([tool_use("t2", "checkout", {"asins": ["TESTPB0001"], "approved_total": 29.99,
                                      "titles": ["Anker Power Bank"]})], "tool_use"),
        ([text("Here's your cart link.")], "end_turn"),
    ])
    tools = FakeTools()
    agent = Agent(tools, db(), send=FakeSender(), client=claude)
    amp = agent.amplitude = FakeAmplitude()
    agent.chat("42", "Buy the Anker power bank")
    for _ in range(50):                      # memory notes are saved in background threads
        if sum(1 for c in tools.calls if c[0] == "remember") >= 2:
            break
        time.sleep(0.05)
    kinds = [k for k, _ in amp.events]
    assert kinds[0] == "agent_turn_started" and "agent_turn_finished" in kinds
    tool_events = [p for k, p in amp.events if k == "agent_tool_called"]
    assert [e["tool"] for e in tool_events] == ["find_offers", "checkout"] and all(e["ok"] for e in tool_events)
    assert len({p["trace_id"] for _, p in amp.events}) == 1          # one trace for the whole turn
    notes = [c[1] for c in tools.calls if c[0] == "remember"]
    assert any("searched Amazon for power bank" in n and "Anker Power Bank at $29.99" in n for n in notes)
    assert any("approved Anker Power Bank for $29.99" in n for n in notes)


def test_no_amplitude_key_means_no_tracking():
    agent = Agent(FakeTools(), db(), send=FakeSender(), client=FakeClaude([([text("hi")], "end_turn")]))
    agent.amplitude = None
    assert agent.chat("42", "hello") == "hi"


# --- Telegram and pairing -------------------------------------------------------

class FakeTelegramHTTP:
    def __init__(self, fail_photo=False):
        self.calls, self.fail_photo = [], fail_photo

    def __call__(self, method, url, body=None, timeout=40):
        name = url.rsplit("/", 1)[1]
        self.calls.append((name, body))
        if name == "sendPhoto" and self.fail_photo:
            raise TelegramError("wrong file identifier/HTTP URL specified")
        if name == "getUpdates":
            return [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "hi"}}]
        return {"ok": True, "username": "pattie_test_bot"} if name == "getMe" else {}


def test_telegram_long_text_split_and_photo_fallback():
    http = FakeTelegramHTTP(fail_photo=True)
    bot = TelegramBot("123:ABC", http=http)
    bot.text(42, "x" * 9000)
    assert [n for n, _ in http.calls].count("sendMessage") == 3
    bot.photo(42, "https://example.com/p.jpg", "Option A")
    assert http.calls[-1][0] == "sendMessage" and "https://example.com/p.jpg" in http.calls[-1][1]["text"]
    assert list(bot.updates())[0]["update_id"] == 7 and bot.offset == 8


def test_pairing_then_only_owner_is_answered():
    owner = Owner(db(), pairing_code="ABC123")
    agent = SimpleNamespace(chat=lambda chat_id, t: f"echo {t}", reset=lambda chat_id: None)
    sender = FakeSender()
    msg = lambda chat, t: {"message": {"chat": {"id": chat}, "text": t}}
    handle_message(msg(99, "hello"), agent, sender, owner)          # nobody paired: ignored
    assert sender.texts == []
    handle_message(msg(42, "/start WRONG"), agent, sender, owner)
    assert owner.chat_id is None
    handle_message(msg(42, "/start ABC123"), agent, sender, owner)  # pairs
    assert owner.chat_id == "42" and "I'm Pattie" in sender.texts[-1][1]
    handle_message(msg(99, "/start ABC123"), agent, sender, owner)  # a second chat can't take over
    assert owner.chat_id == "42"
    handle_message(msg(42, "find a cable"), agent, sender, owner)
    assert sender.texts[-1] == (42, "echo find a cable")
    handle_message(msg(99, "find a cable"), agent, sender, owner)   # not the owner: ignored
    assert sender.texts[-1] == (42, "echo find a cable")


def test_agent_delivery_waits_for_pairing():
    owner = Owner(db(), pairing_code="ABC123")
    agent = SimpleNamespace(event=lambda chat_id, kind, payload: f"{kind} for {payload['event']['title']}")
    sender = FakeSender()
    delivery = AgentDelivery(agent, sender, owner)
    payload = {"kind": "calendar_event", "event": {"id": "e1", "title": "Hackathon SF"},
               "decision": {"items": [{"item": "power bank"}]}}
    try:
        delivery.run("Pattie", "Calendar event", payload)
        assert False, "should wait for pairing"
    except DeliveryError as e:
        assert "paired" in str(e)
    owner.try_pair(42, "/start ABC123")
    delivery.run("Pattie", "Calendar event", payload)
    assert sender.texts == [("42", "calendar_event for Hackathon SF")]
    delivery.run("Pattie", "Food nudge", {"kind": "food_nudge", "event": {"title": "Hackathon SF"}, "nudge": {}})
    assert sender.texts[-1][1].startswith("food_nudge")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
