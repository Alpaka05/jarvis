"""Streaming LLM → Sprachausgabe: Provider, Agent (Anrede, Ersatz-Provider), Satzerkennung, SpeechStream."""
import threading
from types import SimpleNamespace

import config as config_module
import core.voice as voice_module
from core.agent import JarvisAgent
from core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall
from core.voice import SpeechStream, take_sentences
from tests.test_agent import EchoTool, FakeProvider


class StreamingProvider(LLMProvider):
    """Liefert jede Antwort in kleinen Stücken; `fail_after` bricht nach so vielen Stücken ab."""

    name = "stream"
    model = "s-1"

    def __init__(self, responses, chunk=4, fail_after=None):
        self.responses = list(responses)
        self.chunk = chunk
        self.fail_after = fail_after

    def chat(self, system, messages, tools):
        return self.responses.pop(0)

    def chat_stream(self, system, messages, tools, on_text):
        response = self.responses.pop(0)
        for n, i in enumerate(range(0, len(response.text), self.chunk)):
            if self.fail_after is not None and n >= self.fail_after:
                raise LLMError("Verbindung weg")
            on_text(response.text[i : i + self.chunk])
        return response


# ── Satzerkennung für wachsenden Text ────────────────────────────────────────


def test_take_sentences_waits_for_next_sentence_and_lets_first_one_out_early():
    done, rest = take_sentences("Okay. Das Licht", first=True)
    assert done == ["Okay."] and rest == "Das Licht"
    done, rest = take_sentences("Es ist 3.5 Grad warm", first=False)
    assert done == [] and rest == "Es ist 3.5 Grad warm"  # keine Satzgrenze in Zahlen


def test_take_sentences_merges_short_sentences_after_the_first():
    done, rest = take_sentences("Ja. Gut. Das ist ein etwas längerer Satz. Und", first=False)
    assert done == ["Ja. Gut. Das ist ein etwas längerer Satz."]
    assert rest == "Und"
    done, rest = take_sentences("Kurz. Und", first=False)
    assert done == [] and rest == "Kurz. Und"  # wartet auf mehr


def test_streamed_sentences_match_split_sentences():
    text = "Sir, es ist zwölf Uhr. Draußen sind es 18 Grad und sonnig. Morgen wird es regnen, nimm einen Schirm mit."
    buffer, out = "", []
    for i in range(0, len(text), 5):
        buffer += text[i : i + 5]
        done, buffer = take_sentences(buffer, first=not out)
        out += done
    out.append(buffer.strip())
    assert " ".join(out) == text
    assert out[0] == "Sir, es ist zwölf Uhr."


# ── Agent ────────────────────────────────────────────────────────────────────


def test_agent_streams_and_returns_exactly_what_was_streamed(monkeypatch):
    monkeypatch.setattr(config_module.config, "SALUTATION", "")
    agent = JarvisAgent(provider=StreamingProvider([LLMResponse(text="Es ist zwölf Uhr.")]), tools=[])
    chunks = []
    answer = agent.process_query("Uhrzeit?", on_text=chunks.append)
    assert len(chunks) > 1  # kam in Stücken an
    assert "".join(chunks) == answer == "Es ist zwölf Uhr."


def test_streaming_adds_salutation_once_across_tool_steps(monkeypatch):
    monkeypatch.setattr(config_module.config, "SALUTATION", "Sir")
    provider = StreamingProvider([
        LLMResponse(text="Ich schaue nach.", tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})]),
        LLMResponse(text="Sir, das Licht ist aus."),
    ])
    agent = JarvisAgent(provider=provider, tools=[EchoTool()])
    chunks = []
    answer = agent.process_query("Licht?", on_text=chunks.append)
    assert answer == "Sir, ich schaue nach. Das Licht ist aus."
    assert "".join(chunks) == answer
    # Im Verlauf steht, was das Modell geschrieben hat
    assert agent.history[-1]["content"] == "Sir, das Licht ist aus."


def test_streaming_without_salutation_passes_text_through_immediately(monkeypatch):
    monkeypatch.setattr(config_module.config, "SALUTATION", "")
    agent = JarvisAgent(provider=StreamingProvider([LLMResponse(text="Hallo Welt, wie geht's?")], chunk=3), tools=[])
    first_chunk = []
    agent.process_query("hi", on_text=lambda t: first_chunk.append(t) if not first_chunk else None)
    assert first_chunk == ["Hal"]


def test_streaming_uses_fallback_when_nothing_was_said_yet(monkeypatch):
    monkeypatch.setattr(config_module.config, "SALUTATION", "")
    primary = StreamingProvider([LLMResponse(text="nie")], fail_after=0)
    fallback = StreamingProvider([LLMResponse(text="Vom Ersatz.")])
    agent = JarvisAgent(provider=primary, fallback=fallback, tools=[], on_notice=lambda m: None)
    chunks = []
    assert agent.process_query("x", on_text=chunks.append) == "Vom Ersatz."
    assert "".join(chunks) == "Vom Ersatz."


def test_streaming_does_not_restart_on_fallback_after_partial_answer(monkeypatch):
    monkeypatch.setattr(config_module.config, "SALUTATION", "")
    primary = StreamingProvider([LLMResponse(text="Das Wetter morgen wird")], fail_after=2)
    fallback = StreamingProvider([LLMResponse(text="Doppelt.")])
    agent = JarvisAgent(provider=primary, fallback=fallback, tools=[], on_notice=lambda m: None)
    chunks = []
    answer = agent.process_query("Wetter?", on_text=chunks.append)
    assert fallback.responses  # Ersatz wurde nicht gefragt
    assert answer.startswith("Das Wett ") and "nicht erreichbar" in answer
    assert "".join(chunks) == answer
    assert agent.history == []  # unvollständige Runde zurückgenommen


def test_default_chat_stream_emits_whole_text_once():
    chunks = []
    response = FakeProvider([LLMResponse(text="Fertig.")]).chat_stream("", [], [], chunks.append)
    assert chunks == ["Fertig."] and response.text == "Fertig."


# ── SpeechStream ─────────────────────────────────────────────────────────────


class RecordingEngine:
    """Ersetzt VoiceEngine: merkt sich Äußerungen statt sie abzuspielen."""

    clean_text_for_speech = staticmethod(voice_module.VoiceEngine.clean_text_for_speech)

    def __init__(self):
        self.utterances = []

    def _start_utterance(self, listen_for_interrupt):
        u = voice_module._Utterance()
        self.utterances.append(u)
        return u


def _sentences(u):
    u.finish()
    return list(u.sentences())


def test_speech_stream_speaks_sentences_as_they_complete():
    engine = RecordingEngine()
    s = SpeechStream(engine)
    for piece in ["Sir, das ", "Licht ist aus. Im Bad ", "brennt **noch** eine Lampe, soll ich sie ", "ausschalten?"]:
        s.feed(piece)
    assert len(engine.utterances) == 1
    u = engine.utterances[0]
    assert u._sentences == ["Sir, das Licht ist aus."]  # schon unterwegs, bevor der Rest da ist
    s.close()
    assert _sentences(u) == ["Sir, das Licht ist aus.", "Im Bad brennt noch eine Lampe, soll ich sie ausschalten?"]


def test_speech_stream_continues_in_new_utterance_after_interruption():
    engine = RecordingEngine()
    s = SpeechStream(engine)
    s.feed("Ich schaue nach. Moment")
    engine.utterances[0].stop()  # z.B. Rückfrage per speak()
    s.feed(" bitte. Erledigt, die Mail ist raus. ")
    s.close()
    assert len(engine.utterances) == 2
    assert _sentences(engine.utterances[1]) == ["Moment bitte. Erledigt, die Mail ist raus."]


def test_speech_stream_is_silent_after_cancel():
    engine = RecordingEngine()
    s = SpeechStream(engine)
    s.cancelled = True  # VoiceEngine.stop()
    s.feed("Das hört keiner. Wirklich nicht.")
    s.close()
    assert engine.utterances == []


def test_utterance_sentences_wait_for_new_text():
    u = voice_module._Utterance()
    got = []
    reader = threading.Thread(target=lambda: got.extend(u.sentences()))
    reader.start()
    u.add("Eins.")
    u.add("Zwei.")
    u.finish()
    reader.join(2)
    assert got == ["Eins.", "Zwei."]


# ── Provider ─────────────────────────────────────────────────────────────────


def test_openai_stream_assembles_text_and_tool_calls():
    from core.llm.openai_provider import OpenAICompatProvider

    def chunk(content=None, tool_calls=None, finish=None, usage=None):
        delta = SimpleNamespace(content=content, tool_calls=tool_calls)
        choices = [SimpleNamespace(delta=delta, finish_reason=finish)] if usage is None else []
        return SimpleNamespace(choices=choices, usage=usage)

    def tc(index, id=None, name=None, args=None):
        return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=args))

    chunks = [
        chunk("Ich "),
        chunk("schaue."),
        chunk(tool_calls=[tc(0, "call_a", "echo", '{"te')]),
        chunk(tool_calls=[tc(0, args='xt": "hi"}')]),
        chunk(finish="tool_calls"),
        chunk(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None)),
    ]
    provider = OpenAICompatProvider.__new__(OpenAICompatProvider)
    provider.name, provider.model = "openai", "m"
    import openai

    provider._openai = openai
    seen = {}

    def create(**params):
        seen.update(params)
        return iter(chunks)

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    out = []
    response = provider.chat_stream("sys", [{"role": "user", "content": "x"}], [], out.append)
    assert seen["stream"] is True
    assert out == ["Ich ", "schaue."]
    assert response.text == "Ich schaue."
    assert response.tool_calls[0].to_dict() == {"id": "call_a", "name": "echo", "arguments": {"text": "hi"}}
    assert response.usage.input_tokens == 10 and response.usage.output_tokens == 5


def test_gemini_stream_merges_text_and_keeps_function_calls_and_signatures():
    from google.genai import types

    from core.llm.gemini_provider import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    provider._types = types

    def chunk(*parts, usage=None, finish=None):
        cand = SimpleNamespace(content=types.Content(role="model", parts=list(parts)), finish_reason=finish)
        return SimpleNamespace(candidates=[cand], usage_metadata=usage)

    call = types.Part(function_call=types.FunctionCall(name="echo", args={"text": "x"}), thought_signature=b"sig")
    chunks = [
        chunk(types.Part(text="Gedanke", thought=True)),
        chunk(types.Part(text="Moment, ")),
        chunk(types.Part(text="ich schaue.")),
        chunk(call, finish="STOP", usage=SimpleNamespace(prompt_token_count=7, candidates_token_count=3)),
    ]
    out = []
    parts, finish, meta = provider._collect_stream(iter(chunks), out.append)
    assert out == ["Moment, ", "ich schaue."]  # keine Gedanken vorlesen
    assert [p.text for p in parts[:2]] == ["Gedanke", "Moment, ich schaue."]
    assert parts[2].thought_signature == b"sig"
    assert finish == "STOP" and meta.prompt_token_count == 7


def test_gemini_stream_rotates_model_only_before_text(monkeypatch):
    from google.genai import types

    from core.llm.gemini_provider import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    provider._types = types
    provider.models = ["a", "b"]
    provider.model = "a"
    provider._cooldown_until = {}

    def ok_stream():
        cand = SimpleNamespace(content=types.Content(role="model", parts=[types.Part(text="Hallo.")]), finish_reason="STOP")
        yield SimpleNamespace(candidates=[cand], usage_metadata=None)

    def busy_stream():
        raise RuntimeError("429 RESOURCE_EXHAUSTED retry in 5s")
        yield  # pragma: no cover

    streams = {"a": busy_stream, "b": ok_stream}
    provider.client = SimpleNamespace(
        models=SimpleNamespace(generate_content_stream=lambda model, contents, config: streams[model]())
    )
    out = []
    response = provider.chat_stream("", [{"role": "user", "content": "hi"}], [], out.append)
    assert out == ["Hallo."] and response.text == "Hallo." and provider.model == "b"

    def broken_after_text():
        cand = SimpleNamespace(content=types.Content(role="model", parts=[types.Part(text="Ha")]), finish_reason=None)
        yield SimpleNamespace(candidates=[cand], usage_metadata=None)
        raise RuntimeError("503 UNAVAILABLE")

    provider._cooldown_until = {}
    streams["a"] = broken_after_text
    try:
        provider.chat_stream("", [{"role": "user", "content": "hi"}], [], out.append)
    except LLMError as e:
        assert "abgebrochen" in str(e)
    else:
        raise AssertionError("hätte abbrechen müssen")


def test_anthropic_stream_emits_text_and_returns_final_message():
    from core.llm.anthropic_provider import AnthropicProvider

    class Block(SimpleNamespace):
        def model_dump(self, exclude_none=True):
            return dict(self.__dict__)

    final = SimpleNamespace(
        content=[Block(type="text", text="Hallo"), Block(type="text", text="Welt"),
                 Block(type="tool_use", id="t1", name="echo", input={"text": "x"})],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=3, output_tokens=2, cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )
    events = [
        SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="text")),
        SimpleNamespace(type="text", text="Hallo"),
        SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="text")),
        SimpleNamespace(type="text", text="Welt"),
        SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="tool_use")),
    ]

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(events)

        def get_final_message(self):
            return final

    import anthropic

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._anthropic = anthropic
    provider.model, provider.effort = "claude-x", "medium"
    provider.client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **params: FakeStream()))
    out = []
    response = provider.chat_stream("sys", [{"role": "user", "content": "hi"}], [], out.append)
    assert "".join(out) == response.text == "Hallo\nWelt"
    assert response.tool_calls[0].name == "echo"
