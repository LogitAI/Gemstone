import logging
import re
from re import finditer, DOTALL
from dataclasses import dataclass
from typing import Generator, Optional, List, Dict, Union

from .config import ChatHistory
from ..engine import Engine, ModelBusy
from ..utils import FunctionCalling, FunctionCallResult

log = logging.getLogger("gemstone.model")


@dataclass
class Tags:
    """
    Tags for the model, used for filtering and searching.
    """
    REASONING: str = "<think>"
    REASONING_END: str = "</think>"
    TOOLCALL: str = "<tool_call>"
    TOOLCALL_END: str = "</tool_call>"


class BaseModel:
    """
    Base model class that can be extended by other models: the system prompt, sampling defaults and
    server-side tools around one engine. The registry (`registry.py`, SPEC S1.14) creates one per
    loaded engine and owns its lifetime; a model class is not a singleton.
    """
    model_id = ""
    context_length = 0
    engine_options: dict = {}  # extra keyword arguments for Engine (dtype, device, quantization, ...)
    supported_tools: FunctionCalling = FunctionCalling.DEFAULT
    special_tags = Tags()

    def __init__(self, engine: Engine | None = None):
        self.runtime = engine if engine is not None else self._create_engine()

    def _create_engine(self) -> Engine:
        return Engine(self.model_id, context_length=self.context_length or None, **self.engine_options)

    def parse_tool_calling(
        self,
        outputs,
        chat_history: ChatHistory,
        tools: List[Dict[str, str]],
        stream: bool = True,
        print_output: bool = False,
        tool_call_caches: Optional[dict] = None
    ) -> Union[Generator[str, None, None], str]:
        """ Parse tool calling from the model's output """
        result_obj = FunctionCallResult()
        result_obj.register_tools(tools, self.supported_tools.implementations)

        if stream:
            started = False
            buffer = ""
            for word in outputs:
                if self.special_tags.TOOLCALL in word:  # Start of a tool call
                    started = True
                    if buffer:
                        buffer = ""
                elif self.special_tags.TOOLCALL_END in word:  # End of a tool call
                    if buffer:
                        result_obj.stage(
                            buffer,
                            (self.special_tags.TOOLCALL, self.special_tags.TOOLCALL_END),
                            tool_call_caches
                        )
                        state = result_obj.state
                        if state is not None:
                            yield state
                    buffer = ""
                    started = False
                else:
                    if started:
                        buffer += word
                        state = result_obj.state
                        if state is not None:
                            yield state
                    else:
                        yield word
        else:
            original_outputs = outputs
            outputs = outputs.replace(self.special_tags.TOOLCALL, "").replace(self.special_tags.TOOLCALL_END, "")

            for match in finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", original_outputs, DOTALL):
                json_string = match.group(1)  # Extract the JSON string from the match
                outputs.replace(json_string, "")  # Remove the tool call from the output
                result_obj.stage(
                    json_string,
                    (self.special_tags.TOOLCALL, self.special_tags.TOOLCALL_END),
                    tool_call_caches
                )

        # Finalize the tool calls
        waiting = False
        while True:
            queued = len(result_obj.job_list)
            final_result = result_obj.finalize(
                chat_history,
                (self.special_tags.TOOLCALL, self.special_tags.TOOLCALL_END)
            )
            if queued > 0 and final_result is False and not waiting:
                waiting = True
                log.debug("Waiting for %d tool call(s) to finish", queued)
            if final_result is False:
                result_obj.wait(0.1)  # sleeps until a tool finishes
                continue
            if queued > 0:
                log.debug("Tool calls are finalized")

            if stream:
                yield final_result
                break
            else:
                return outputs + final_result

    def chat(
        self,
        chat_history: ChatHistory,
        user_prompt: str,
        system_prompt: str = "",
        tools: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        top_k: int = 40,
        min_p: float = 0.05,
        typical_p: float = 1.0,
        stream: bool = True,
        max_new_tokens: int = 1024,
        repeat_penalty: float = 1.0,
        print_output: bool = False,
        tool_call_caches: Optional[dict] = None,
        **kwargs
    ) -> Union[Generator[str, None, None], str]:
        """ Process a chat request

        Args:
            chat_history (ChatHistory): Chat history
            user_prompt (str): User prompt
            system_prompt (str, optional): System prompt. Defaults to "".
            tools (Optional[List[Dict[str, str]]], optional): Tools. Defaults to None. Pass empty list to disable tools.
            temperature (float, optional): Temperature. Defaults to 0.2.
            top_p (float, optional): Top p. Defaults to 0.95.
            top_k (int, optional): Top k. Defaults to 40.
            min_p (float, optional): Min p. Defaults to 0.05.
            typical_p (float, optional): Typical p. Defaults to 1.0.
            stream (bool, optional): Stream. Defaults to True.
            max_new_tokens (int, optional): Max new tokens. Defaults to 1024.
            repeat_penalty (float, optional): Repeat penalty. Defaults to 1.0.
            print_output (bool, optional): Ignored, kept for callers. Prompts and answers are logged at DEBUG
                (GEMSTONE_LOG_LEVEL=DEBUG), never printed.
            tool_call_caches (dict, optional): The session's tool-result cache (call id -> result).
            **kwargs: Additional arguments
        """
        def adaptive_special_tag_buffering(outs, wait_tokens_for=6):
            """
            Adaptive buffering for special tags in the output stream.
            Needed for models that uses fine-grained tokenizer which can produce special tags separately.
            """
            buffer = ""
            patient = wait_tokens_for
            for wd in outs:
                if "<" in wd or buffer:  # Start of special tag
                    patient -= 1
                    buffer += wd
                    if ">" in buffer or patient == 0:  # Incomplete tag
                        yield buffer
                        buffer = ""
                        patient = wait_tokens_for
                else:
                    yield wd

        tag_pattern = re.compile("(" + "|".join(re.escape(t) for t in (
            self.special_tags.REASONING, self.special_tags.REASONING_END,
            self.special_tags.TOOLCALL, self.special_tags.TOOLCALL_END,
        )) + ")")

        def split_special_tags(outs):
            """
            Give every special tag a frame of its own. The app matches `<think>`/`</think>` exactly, and a
            streamer that emits at word boundaries glues tags to whitespace or text (`'<think>\\n'`).
            """
            for wd in outs:
                for piece in tag_pattern.split(wd):
                    if piece:
                        yield piece

        initial_operation = True
        function_called = True
        while function_called:
            function_called = False

            prompt = chat_history.create_prompt(system_prompt, user_prompt)
            if user_prompt is not None:
                chat_history.append("user", user_prompt)
            else:
                prompt = prompt[:-1]  # Remove the last user prompt if it's None
            user_prompt = None  # Reset user prompt to None after appending

            if initial_operation and log.isEnabledFor(logging.DEBUG):
                log.debug("PROMPT:\n%s", "\n".join(str(line) for line in prompt))
            if initial_operation:
                initial_operation = False
                # TODO: Add kv cache control for tool-calling here

            tools = tools if tools is not None else self.supported_tools.schemas

            generation_kwargs = dict(
                messages=prompt,
                tools=tools if tools else None,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                min_p=min_p,
                typical_p=typical_p,
                stream=stream,
                max_new_tokens=max_new_tokens,
                repeat_penalty=repeat_penalty
            )
            generation_kwargs.update(kwargs)
            outputs = self.parse_tool_calling(
                split_special_tags(adaptive_special_tag_buffering(self.runtime(**generation_kwargs))),
                chat_history=chat_history,
                tools=tools,
                stream=stream,
                tool_call_caches=tool_call_caches
            )

            if stream:
                answer = []
                try:
                    for word in outputs:
                        if word:
                            if self.special_tags.TOOLCALL in word and self.special_tags.TOOLCALL_END in word:
                                function_called = True  # flag on
                            answer.append(word)
                            yield word
                except ValueError as e:  # Over token limit error
                    log.exception("Chat failed")
                    if "token" in str(e) and "limit" in str(e):
                        message = "\n\nERROR: Chat is unexpectedly terminated due to token limit. Please shorten your prompt or chat history."
                    else:
                        message = f"\n\nERROR: {type(e)} - Something went wrong while processing the chat. Please try again later.\n{e}"
                    answer.append(message)
                    yield message
                except ModelBusy:
                    raise  # the server maps it to 503 / WebSocket close 1013, so it must not become text
                except Exception as e:  # never let a failure kill the stream without a message
                    log.exception("Chat failed")
                    message = f"\n\nERROR: {type(e).__name__} - Something went wrong while processing the chat.\n{e}"
                    answer.append(message)
                    yield message
                finally:
                    if log.isEnabledFor(logging.DEBUG):
                        log.debug("ANSWER:\n%s", "".join(answer))
            else:
                if self.special_tags.TOOLCALL in outputs and self.special_tags.TOOLCALL_END in outputs:
                    function_called = True  # flag on
                log.debug("ANSWER:\n%s", outputs)
