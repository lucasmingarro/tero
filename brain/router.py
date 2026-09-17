"""Tero's brain: a local model picks and runs tools (tool calling).

qwen3:4b-instruct is used instead of plain qwen3:4b: the base variant
forces a <think> block in its chat template no matter what (even when
asked with think=False), which added ~15-25s of visible reasoning to every
answer. The instruct variant does not reason and answers in <1s warm.

Two hard, non-optional safeguards on every call to the model:
- `num_predict`: with no cap, this model has been seen looping and
  generating 30000+ tokens without stopping (it never emitted the end
  token), pinning the GPU for several minutes on a single query.
- a client `timeout`: a spare net for the case where, even with the token
  cap, something hangs on the server side.
"""

import re

from ollama import Client

import tools.clock  # noqa: F401
import tools.maps  # noqa: F401
import tools.move_window  # noqa: F401
import tools.music  # noqa: F401
import tools.phone  # noqa: F401
import tools.terminal  # noqa: F401
import tools.volume  # noqa: F401
import tools.weather  # noqa: F401  (registers the tool)
import tools.web  # noqa: F401
import tools.youtube  # noqa: F401
from brain.prompt import SYSTEM_PROMPT
from tools import catalog, execute, is_error

_MAX_RESPONSE_TOKENS = 200
_TIMEOUT_S = 10.0

# Warming the model up (see `preload`) gets its own, much longer timeout.
# With the 10s above, right after a machine restart, the client gave up
# before it finished, and Ollama *aborts* what it was doing when the client
# disconnects ("client connection closed before llama-server finished
# loading, aborting load"). The next request started from scratch and was
# cut off again: a loop that never unstuck itself, with Tero answering "se
# colgó el modelo local".
_LOAD_TIMEOUT_S = 180.0

# How many tool-calling rounds are allowed within one turn. Without this, a
# compound request ("buscá X y mandámelo al celular") was left half done:
# the model called a single tool and then made up in its summary that it
# had done the second action too. With several rounds it can ask for a
# tool, see the result, and ask for another if it is still needed. The cap
# is deliberately low: every round is a full call to the model.
_MAX_TOOL_ROUNDS = 4

# For these tools a spoken confirmation is redundant: the music starting to
# play (or the audio stopping) is the answer. If one of them fails, the
# silence is broken anyway (see below) to report that something happened.
_SILENT_TOOLS = {"play_music", "play_random_music", "control_playback"}

# How many previous exchanges (user+assistant) are passed to the model.
# Without this every sentence starts from zero: "poné metallica" ->
# "pausalo" has no way of knowing what "lo" refers to. Not much room is
# needed (this does not bring back follow-up questions, which are still
# forbidden: the user has to say something that stands on its own, not a
# "sí"/"no" that depends on the model remembering what it asked -- here at
# least it does remember).
_HISTORY_TURNS = 2


def _strip_follow_up_question(text: str) -> str:
    """Cuts off any question left hanging at the end of a spoken answer.

    The prompt forbids asking, but the model sometimes does it anyway (seen
    live: 'de nada, ¿qué tal si ahora ponemos música?'). The problem is not
    just that it sounds odd: that question stays in the history, and the
    next turn -- even a completely unrelated one -- gets read as if it were
    the answer to it (seen live: after that question, "dos más dos" fired
    play_random_music instead of answering the sum). This is a
    deterministic filter, not one more rule for the model to decide whether
    to follow.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    while sentences and sentences[-1].rstrip().endswith("?"):
        sentences.pop()
    return " ".join(sentences).strip() or text.strip()


class Brain:
    def __init__(self, model: str = "qwen3:4b-instruct"):
        self._model = model
        self._client = Client(timeout=_TIMEOUT_S)
        self._load_client = Client(timeout=_LOAD_TIMEOUT_S)
        # A list per turn, not a flat list of messages: a turn with tools
        # spans several messages (an assistant one with tool_calls plus one
        # tool message per result) and trimming by message count could cut
        # it in half, leaving an orphan "tool" without the call that
        # produced it. Trimming whole turns cannot do that.
        self._history: list[list[dict]] = []

    def _is_loaded(self) -> bool:
        try:
            return any(m.model == self._model for m in self._client.ps().models)
        except Exception:
            return False

    def preload(self) -> None:
        """Leaves the model loaded with the system prompt already processed.

        Loading the model is not enough. Measured right after a machine
        restart: the load itself took 3.3s, but then the first query has to
        process the system prompt plus the tool catalog (~2300 tokens) with
        nothing cached and a third of the model on CPU, and that alone went
        past 10s. Later queries are fast because Ollama reuses that already
        processed prefix. That is why the warmup is a query with the same
        system prompt and the same tools, which leaves the prefix cached,
        and not an empty prompt.

        Called at startup and before every query: Ollama unloads the model
        after 5 minutes idle, and the cache is lost then. If the model is
        still loaded, this is a cheap local call.
        """
        if self._is_loaded():
            return
        self._load_client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "hola"},
            ],
            tools=catalog(),
            options={"num_predict": 1, "temperature": 0.2},
        )

    def _chat(self, messages: list, with_tools: bool):
        self.preload()
        return self._client.chat(
            model=self._model,
            messages=messages,
            tools=catalog() if with_tools else None,
            # Low temperature: less variety in how it speaks, in exchange
            # for more predictable answers. Careful with the history of
            # this one: it was lowered believing sampling was why the model
            # "described" the tool call instead of emitting it, and it was
            # not that (it was the prose history, see _respond) -- which is
            # why lowering it never quite fixed that.
            options={"num_predict": _MAX_RESPONSE_TOKENS, "temperature": 0.2},
        )

    def respond(self, user_text: str) -> str:
        response_text, turn_messages = self._respond(user_text)
        # Only turns that used a tool make it into the history. A
        # text-only turn adds nothing that is needed later (the history
        # exists for "poné metallica" -> "pausala", and that is a turn with
        # a tool), and it can poison the next one: if Whisper hears "Buena
        # música." instead of "Poné música." and the model answers
        # "perfecto", that exchange teaches it that a music request is
        # answered by writing. Measured with qwen3:4b-instruct: "Poné
        # música." gives 20/20 tool calls with no history and 13/20 behind
        # that turn -- the other 7 times it says "play_random_music" out
        # loud instead of calling it. Behind a turn WITH a tool it stays
        # 20/20, and so does "pausala".
        if any(message.get("role") == "tool" for message in turn_messages):
            self._history.append([{"role": "user", "content": user_text}, *turn_messages])
            self._history = self._history[-_HISTORY_TURNS:]
        return response_text

    def _history_messages(self) -> list[dict]:
        return [message for turn in self._history for message in turn]

    def _respond(self, user_text: str) -> tuple[str, list[dict]]:
        """Returns (what gets said out loud, the messages kept in history).

        The history stores the native tool-calling transcript as is (an
        "assistant" message with `tool_calls` and a "tool" message with the
        result), which is the format the model was trained on.

        This used to build a prose note instead ("in the previous turn you
        used tool X and the result was: Y") and that was a serious bug: on
        a repeated request, the model read that note as "this is answered
        by writing" and answered in prose instead of calling the tool
        again. Measured with qwen3:4b-instruct on a repeated "siguiente
        canción": 0/12 tool calls with the prose note, 12/12 with the
        native transcript. It did not depend on how the note was worded --
        stripping the result text out of it also gave 0/12 -- so it is not
        fixed by polishing the sentence. Follow-up references ("poné
        metallica" -> "pausala"), which were the reason for having a
        history at all, still work 12/12.
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self._history_messages(),
            {"role": "user", "content": user_text},
        ]
        seen: set = set()
        results: list = []
        transcript: list[dict] = []

        for round_index in range(_MAX_TOOL_ROUNDS):
            try:
                response = self._chat(messages, with_tools=True)
            except Exception as error:
                text = f"Se colgó el modelo local: {error}"
                return text, [{"role": "assistant", "content": text}]
            message = response.message
            if not message.tool_calls:
                if round_index == 0:
                    # It never asked for any tool: plain conversation.
                    #
                    # A safety net was evaluated here (2026-09-14) and
                    # dropped: comparing user_text against the learned
                    # YouTube channels, for the case where the
                    # transcription eats the whole verb of the request
                    # ("Poné Vorterix" -> "Bueno, Bortelix", seen live).
                    # The problem is not the threshold: "hola" scores 0.75
                    # against "olga" (same four letters), identical to
                    # "bortelix" against "vorterix" -- there is no way to
                    # tell those two cases apart with plain text
                    # similarity. Tested live with the net in place:
                    # "pausa la música" opened "Parén la Mano", "dale,
                    # mirá esto" opened "Radio Mitre". A false positive
                    # there is worse than the generic conversation here
                    # (it changes what the user is watching without being
                    # asked), so it was removed -- see BITACORA.html,
                    # 2026-09-14, for the full detail.
                    text = _strip_follow_up_question((message.content or "").strip())
                    return text, [{"role": "assistant", "content": text}]
                break  # no more tools requested, on to the summary

            executed = []
            for call in message.tool_calls:
                key = (call.function.name, tuple(sorted(call.function.arguments.items())))
                if key in seen:
                    # Qwen3 4B sometimes repeats the same call several
                    # times (seen with "bajame el volumen 20%" -> 3
                    # identical calls).
                    continue
                seen.add(key)
                result = execute(call.function.name, call.function.arguments)
                print(f"  tool_call: {call.function.name}({call.function.arguments}) -> {result!r}")
                executed.append((call.function.name, dict(call.function.arguments), result))
            if not executed:
                break  # only repeats of what was already done, nothing new

            # The model's message is rebuilt with the calls that actually
            # ran (not the duplicates that were filtered out): that way
            # every tool_call in the transcript has its result, with no
            # gaps.
            assistant_message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": name, "arguments": arguments}}
                    for name, arguments, _result in executed
                ],
            }
            messages.append(assistant_message)
            transcript.append(assistant_message)
            for name, arguments, result in executed:
                tool_message = {"role": "tool", "content": result, "name": name}
                messages.append(tool_message)
                transcript.append(tool_message)
                results.append((name, arguments, result))

        if results and all(
            name in _SILENT_TOOLS and not is_error(result)
            for name, _args, result in results
        ):
            return "", transcript

        try:
            final_response = self._chat(messages, with_tools=False)
        except Exception as error:
            return f"Se colgó el modelo local: {error}", transcript
        text = _strip_follow_up_question((final_response.message.content or "").strip())
        return text, [*transcript, {"role": "assistant", "content": text}]
