import os
import time
import hashlib
import json
from openai import OpenAI
from openai import APITimeoutError, APIError
from . import logger
from .cache import save_json_cache, load_json_cache


# ============================================================================
# LLM Prompt Template for Package Distribution Detection
# ============================================================================
def build_package_distribution_prompt(baseline_package, target_repos):
    """
    Build the LLM prompt for package distribution detection.

    Args:
        baseline_package: PackageInfo object containing baseline package info
        target_repos: List of target repository keys (e.g., ["fedora_42", "arch", "ubuntu_jammy"])

    Returns:
        str: The formatted prompt string
    """
    prompt = (
        f"You are a Linux packaging expert with extensive knowledge of distributing packages across different linux distributions and correpsonding repositories.\n\n"
        f"**Task:** Identify the likely functionally equivalent packages for the provided baseline package across the specified target repositories.\n\n"
        f"**Equivalence Criteria:** Match packages providing the same functionality and role (e.g., dev vs. runtime), but may have different names or versions due to different naming conventions or packaging standards.\n"
        f"**Example:** `liblzma-dev` (Ubuntu) ≈ `xz-devel` (Fedora) ≈ `xz` (Arch).\n\n"
        f"**Baseline Package:**\n"
        f"- Name: `{baseline_package.bin_name}`\n"
        f"- Source Package Name: `{baseline_package.src_name}`\n"
        f"- Description: `{baseline_package.description}`\n"
        f"- Repository Name: `{baseline_package.repo_name}`\n"
        f"- Repository Version: `{baseline_package.repo_version}`\n\n"
        f"**Target Repositories:** {', '.join(target_repos)}\n\n"
        f"**Instructions:**\n"
        f"1. For each target repository, find the most likely functional equivalent.\n"
        f"2. If you are confident about no match for a specific repository, use a `null` value for the repository.\n"
        f"3. Respond ONLY with a flat JSON object inside a ```json code block (no prose). The **keys** must be the exact target repository names provided above, and the **values** must be the package name strings or `null`.\n\n"
        f"**Example Output:**\n"
        f"```json\n"
        f"{{\n"
        f'  "fedora_42": "xz-devel",\n'
        f'  "arch": "xz",\n'
        f'  "ubuntu_jammy": "liblzma-dev",\n'
        f'  "pypi": null\n'
        f"}}\n"
        f"```"
    )
    return prompt

# Global token counters (across all LLMCaller instances)
global_input_token_count = 0
global_output_token_count = 0
global_total_time = 0.0
global_call_count = 0
global_cache_hit_count = 0


# ============================================================================
# Model Presets Configuration
# ============================================================================
# Each preset defines: actual_model, extra_body options, and whether to use stream
MODEL_PRESETS = {
    # OpenRouter - z-ai/glm-4.7 with reasoning
    "glm-4.7": {
        "model": "z-ai/glm-4.7",
        "extra_body": {"provider": {"sort": "price"}, "reasoning": {"enabled": True}},
        "temperature": 0,
        "stream": False,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "reasoning_format": "openrouter",
    },
    # OpenRouter - deepseek/deepseek-v3.2 with reasoning
    "deepseek-v3.2": {
        "model": "deepseek/deepseek-v3.2",
        "extra_body": {"reasoning": {"enabled": True}},
        "temperature": 0,
        "stream": False,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "reasoning_format": "openrouter",
    },
    # OpenRouter - google/gemini-3-pro-preview
    "gemini-3-pro": {
        "model": "google/gemini-3-pro-preview",
        "extra_body": {},
        "temperature": 0,
        "stream": False,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    # OpenRouter - anthropic/claude-opus-4-5
    "claude-opus-4-5": {
        "model": "anthropic/claude-opus-4.5",
        "extra_body": {},
        "temperature": 0,
        "stream": False,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
}


def get_available_presets():
    """Get list of available model presets."""
    return list(MODEL_PRESETS.keys())


def get_global_stats():
    """Get global statistics across all LLMCaller instances."""
    return {
        "input_tokens": global_input_token_count,
        "output_tokens": global_output_token_count,
        "total_time": global_total_time,
        "call_count": global_call_count,
        "cache_hit_count": global_cache_hit_count,
    }


def reset_global_stats():
    """Reset global statistics."""
    global global_input_token_count, global_output_token_count, global_total_time, global_call_count, global_cache_hit_count
    global_input_token_count = 0
    global_output_token_count = 0
    global_total_time = 0.0
    global_call_count = 0
    global_cache_hit_count = 0


def _generate_cache_key(message: str, extra_body: dict = None) -> str:
    """
    Generate a cache key based on message and extra_body.

    Args:
        message: The user message/prompt
        extra_body: Extra body parameters (e.g., enable_thinking, enable_web_search)

    Returns:
        A hash string suitable for cache filename
    """
    # Serialize extra_body to ensure consistent key generation
    extra_str = json.dumps(extra_body, sort_keys=True) if extra_body else ""
    # Combine message and extra_body for unique hash
    key_content = f"{message}:{extra_str}"
    # Use MD5 hash (good enough for caching, fast)
    return hashlib.md5(key_content.encode('utf-8')).hexdigest()


class LLMCaller:
    """
    LLM caller class with support for:
    - Model presets (predefined configurations)
    - Token counting (instance and global)
    - Time tracking
    - Response caching

    Available presets:
    - "glm-4.7": z-ai/glm-4.7 with reasoning (via OpenRouter)
    - "deepseek-v3.2": deepseek/deepseek-v3.2 with reasoning (via OpenRouter)
    - "gemini-3-pro": google/gemini-3-pro-preview (via OpenRouter)
    - "claude-opus-4-5": anthropic/claude-opus-4.5 (via OpenRouter)
    """

    DEFAULT_PRESET = "glm-4.7"
    SYSTEM_PROMPT = "You are a helpful assistant designed to find equivalent software package names across different operating systems. You must provide answers in the specified JSON format."
    
    def __init__(self, preset: str = None, system_prompt: str = None):
        """
        Initialize LLMCaller.

        Args:
            preset: Model preset name. See MODEL_PRESETS for available options.
                   Default is "default" (qwen-plus-latest).
            system_prompt: Custom system prompt. If None, uses default.
        """
        self.preset = preset or self.DEFAULT_PRESET
        self.system_prompt = system_prompt or self.SYSTEM_PROMPT
        self.messages = [{"role": "system", "content": self.system_prompt}]

        # Get preset config to determine base_url and api_key
        config = self._get_preset_config()
        base_url = config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        api_key_env = config.get("api_key_env", "DASHSCOPE_API_KEY")

        self.client = OpenAI(
            api_key=os.getenv(api_key_env),
            base_url=base_url,
        )

        # Store reasoning_format for handling responses
        self.reasoning_format = config.get("reasoning_format", "dashscope")

        # Instance-level statistics
        self.input_token_count = 0
        self.output_token_count = 0
        self.total_time = 0.0
        self.call_count = 0
        self.cache_hit_count = 0


    def _get_preset_config(self) -> dict:
        """Get configuration for current preset."""
        if self.preset in MODEL_PRESETS:
            return MODEL_PRESETS[self.preset]
        else:
            # If preset not found, treat it as a raw model name
            logger.warning(f"Preset '{self.preset}' not found, using as raw model name")
            return {
                "model": self.preset,
                "extra_body": {},
                "stream": False,
            }
    
    def _handle_stream_response(self, response) -> tuple:
        """
        Handle streaming response, collect content and extract usage.

        Returns:
            tuple: (content, thinking_content, prompt_tokens, completion_tokens)
        """
        content_parts = []
        thinking_parts = []
        prompt_tokens = 0
        completion_tokens = 0

        for chunk in response:
            # Extract usage from final chunk
            if hasattr(chunk, 'usage') and chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens or 0
                completion_tokens = chunk.usage.completion_tokens or 0

            # Extract content
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta

                # Regular content
                if hasattr(delta, 'content') and delta.content:
                    content_parts.append(delta.content)

                # Thinking content (for thinking models) - Dashscope format
                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    thinking_parts.append(delta.reasoning_content)

        content = ''.join(content_parts)
        thinking_content = ''.join(thinking_parts) if thinking_parts else None

        return content, thinking_content, prompt_tokens, completion_tokens

    def _handle_non_stream_response(self, response) -> tuple:
        """
        Handle non-streaming response, extract content and usage.
        Supports both Dashscope and OpenRouter reasoning formats.
        Also supports Claude thinking format (response.content array).

        Returns:
            tuple: (content, thinking_content, prompt_tokens, completion_tokens, reasoning_details)
        """
        # Safety check: ensure response has choices
        if not response or not hasattr(response, 'choices') or not response.choices or len(response.choices) == 0:
            logger.warning(f"Invalid response: no choices available. Response: {response}")
            return "", None, 0, 0, None

        message = response.choices[0].message
        
        # Handle Claude thinking format: response.content is an array of blocks
        if self.reasoning_format == "claude" and hasattr(message, 'content') and isinstance(message.content, list):
            content_parts = []
            thinking_parts = []
            for block in message.content:
                if hasattr(block, 'type'):
                    if block.type == "text" and hasattr(block, 'text'):
                        content_parts.append(block.text)
                    elif block.type == "thinking" and hasattr(block, 'thinking'):
                        thinking_parts.append(block.thinking)
            content = ''.join(content_parts)
            thinking_content = '\n'.join(thinking_parts) if thinking_parts else None
        else:
            # Standard format: message.content is a string
            content = message.content if message else ""

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        thinking_content_standard = None
        reasoning_details = None

        if self.reasoning_format == "openrouter":
            # OpenRouter format: reasoning_details
            reasoning_details = getattr(message, 'reasoning_details', None) if message else None
            if reasoning_details:
                thinking_content_standard = reasoning_details
        elif self.reasoning_format != "claude":
            # Dashscope format: reasoning_content
            thinking_content_standard = getattr(message, 'reasoning_content', None) if message else None

        # Use thinking_content from Claude format if available, otherwise use standard format
        if thinking_content is None:
            thinking_content = thinking_content_standard

        return content, thinking_content, prompt_tokens, completion_tokens, reasoning_details
    
    def inference(self, message: str, use_cache: bool = True) -> str:
        """
        Send a message to the LLM and get a response.
        
        Args:
            message: The user message to send
            use_cache: If True (default), check cache before calling LLM and save response to cache.
        
        Returns:
            The LLM response content as a string
        """
        global global_input_token_count, global_output_token_count, global_total_time, global_call_count, global_cache_hit_count

        # Get config for cache key generation
        config = self._get_preset_config()
        extra_body = config.get("extra_body", {})

        # Generate cache key
        cache_key = _generate_cache_key(message, extra_body)
        cache_subdir = f"llm_cache/{self.preset}"

        # Try to load from cache
        if use_cache:
            cached_data = load_json_cache(
                cache_name="llm_response",
                cache_key=cache_key,
                subdir=cache_subdir
            )
            if cached_data is not None:
                logger.info(f"LLM Cache HIT - Preset: {self.preset}, Key: {cache_key[:16]}...")
                self.cache_hit_count += 1
                global_cache_hit_count += 1
                # Log thinking content if available
                thinking_content = cached_data.get("thinking_content")
                if thinking_content:
                    logger.info(f"[Cached] Thinking content: {thinking_content[:200]}..." if len(thinking_content) > 200 else f"[Cached] Thinking content: {thinking_content}")
                return cached_data.get("response", "")

        # Retry logic for API calls
        max_retries = 3
        retry_delay = 1  # seconds
        
        for attempt in range(max_retries):
            try:
                actual_model = config["model"]
                use_stream = config["stream"]
                
                # Build messages (single-turn only for caching consistency)
                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": message},
                ]
                
                # Build API call parameters
                api_params = {
                    "model": actual_model,
                    "messages": messages,
                }
                if extra_body:
                    api_params["extra_body"] = extra_body
                if use_stream:
                    api_params["stream"] = True
                    # For stream mode with usage tracking
                    api_params["stream_options"] = {"include_usage": True}
                # Add temperature if specified in config
                if "temperature" in config:
                    api_params["temperature"] = config["temperature"]
                
                # Track time
                start_time = time.time()
                
                # Make API call
                response = self.client.chat.completions.create(**api_params)

                # Validate response for non-streaming mode
                if not use_stream and (response is None or not hasattr(response, 'choices')):
                    logger.error(f"Invalid API response: response is None or missing 'choices' attribute. Response: {response}")
                    if attempt < max_retries - 1:
                        logger.warning(f"Retrying due to invalid response (attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_delay)
                        continue
                    return ""

                # Handle response based on stream mode
                thinking_content = None
                raw_response_dict = None
                if use_stream:
                    # For streaming, collect chunks AND extract content in a single pass
                    chunks_list = []
                    content_parts = []
                    thinking_parts = []
                    prompt_tokens = 0
                    completion_tokens = 0

                    for chunk in response:
                        # Collect chunk for raw_response
                        chunk_dict = {
                            "content": chunk.choices[0].delta.content if chunk.choices and hasattr(chunk.choices[0].delta, 'content') else None,
                            "reasoning_content": chunk.choices[0].delta.reasoning_content if chunk.choices and hasattr(chunk.choices[0].delta, 'reasoning_content') else None,
                        }
                        if hasattr(chunk, 'usage') and chunk.usage:
                            chunk_dict["usage"] = {
                                "prompt_tokens": chunk.usage.prompt_tokens,
                                "completion_tokens": chunk.usage.completion_tokens,
                            }
                            # Extract usage from final chunk
                            prompt_tokens = chunk.usage.prompt_tokens or 0
                            completion_tokens = chunk.usage.completion_tokens or 0
                        chunks_list.append(chunk_dict)

                        # Extract content for actual response
                        if chunk.choices and len(chunk.choices) > 0:
                            delta = chunk.choices[0].delta
                            if hasattr(delta, 'content') and delta.content:
                                content_parts.append(delta.content)
                            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                                thinking_parts.append(delta.reasoning_content)

                    raw_response_dict = {"streaming_chunks": chunks_list, "stream_mode": True}
                    content = ''.join(content_parts)
                    thinking_content = ''.join(thinking_parts) if thinking_parts else None
                    if thinking_content:
                        logger.info(f"Thinking content: {thinking_content[:200]}..." if len(thinking_content) > 200 else f"Thinking content: {thinking_content}")
                else:
                    content, thinking_content, prompt_tokens, completion_tokens, reasoning_details = self._handle_non_stream_response(response)
                    # For non-streaming, dump the full response object
                    raw_response_dict = response.model_dump() if hasattr(response, 'model_dump') else str(response)
                    if thinking_content:
                        logger.info(f"Thinking content: {thinking_content[:200]}..." if len(thinking_content) > 200 else f"Thinking content: {thinking_content}")

                # Check if content is empty and retry if needed
                if not content or not content.strip():
                    if attempt < max_retries - 1:
                        logger.warning(f"LLM returned empty response (attempt {attempt + 1}/{max_retries}), retrying...")
                        time.sleep(retry_delay)
                        continue

                elapsed_time = time.time() - start_time
                
                # Update instance statistics
                self.input_token_count += prompt_tokens
                self.output_token_count += completion_tokens
                self.total_time += elapsed_time
                self.call_count += 1
                
                # Update global statistics
                global_input_token_count += prompt_tokens
                global_output_token_count += completion_tokens
                global_total_time += elapsed_time
                global_call_count += 1
                
                # Log statistics
                extra_info = []
                if extra_body.get("enable_web_search"):
                    extra_info.append("Web Search")
                if extra_body.get("enable_thinking"):
                    extra_info.append("Thinking")
                extra_str = f", Features: [{', '.join(extra_info)}]" if extra_info else ""
                
                logger.info(f"LLM Call - Preset: {self.preset}, Model: {actual_model}{extra_str}")
                logger.info(f"  Time: {elapsed_time:.2f}s | Tokens: {prompt_tokens} in, {completion_tokens} out")
                
                # Save to cache
                if use_cache and content:
                    try:
                        cache_data = {
                            "preset": self.preset,
                            "model": actual_model,
                            "response": content,
                            "thinking_content": thinking_content,
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                            "elapsed_time": elapsed_time,
                            "timestamp": time.time(),
                            "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                            "raw_response": raw_response_dict,
                        }
                        save_json_cache(
                            cache_name="llm_response",
                            cache_key=cache_key,
                            data=cache_data,
                            subdir=cache_subdir
                        )
                        logger.debug(f"LLM response cached with key: {cache_key[:16]}...")
                    except Exception as e:
                        logger.warning(f"Failed to save LLM response to cache: {e}")

                return content

            except (APITimeoutError, APIError) as e:
                # Retry on timeout or API errors
                if attempt < max_retries - 1:
                    logger.warning(f"LLM API error (attempt {attempt + 1}/{max_retries}), retrying...: {e}")
                    time.sleep(retry_delay)
                    continue
                else:
                    logger.error(f"LLM API error after all retries: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    return ""
            except Exception as e:
                # For other exceptions, also retry
                if attempt < max_retries - 1:
                    logger.warning(f"Error calling LLM (attempt {attempt + 1}/{max_retries}), retrying...: {e}")
                    time.sleep(retry_delay)
                    continue
                else:
                    logger.error(f"Error calling LLM after all retries: {e}")
                    import traceback
                    logger.info(traceback.format_exc())
                    # Log response object if available for debugging
                    if 'response' in locals():
                        logger.error(f"Response type: {type(response)}")
                        logger.error(f"Response: {response}")
                    return ""
    
    def get_stats(self) -> dict:
        """Get instance-level statistics."""
        return {
            "preset": self.preset,
            "input_tokens": self.input_token_count,
            "output_tokens": self.output_token_count,
            "total_tokens": self.input_token_count + self.output_token_count,
            "total_time": self.total_time,
            "call_count": self.call_count,
            "cache_hit_count": self.cache_hit_count,
        }
    
    def reset_history(self):
        """Reset message history to only system prompt."""
        self.messages = [{"role": "system", "content": self.system_prompt}]


# Backward compatibility: keep _call_llm function as a wrapper
def _call_llm(prompt: str, preset: str = "default", use_cache: bool = True) -> str:
    """
    Legacy wrapper function for backward compatibility.
    Creates a one-shot LLMCaller instance for the call.
    
    Args:
        prompt: The prompt to send to the LLM
        preset: Model preset name (see MODEL_PRESETS for options)
               For backward compatibility, also accepts raw model names.
        use_cache: If True (default), check cache before calling LLM.
    
    Returns:
        The LLM response content as a string
    """
    caller = LLMCaller(preset=preset)
    return caller.inference(prompt, use_cache=use_cache)
