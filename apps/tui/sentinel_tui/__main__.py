"""Standalone CLI configuration: explicit provider, model and environment key."""

import argparse
import os
import sys

from sentral.llm.providers.openai import OpenAIProvider
from sentral.llm.providers.anthropic import AnthropicProvider
from sentral.llm.runtime_adapter import SentinelProviderAdapter
from sentinel_tui.ui import Chat
from sentinel_tui.setup import Setup, KEY_NAMES
from sentral.llm.providers.codex import CodexProvider
from sentral.llm.providers.gemini_oauth import GeminiOAuthProvider
from sentral.llm.claude_credentials import renew_claude_access_token
from sentral import GenerationConfig
from sentral.llm.providers.gemini import GeminiProvider


def main():
    parser = argparse.ArgumentParser(description="Sentral terminal chat with local host tools")
    parser.add_argument("--provider", choices=("openai", "anthropic", "gemini"), default=None)
    parser.add_argument("--model")
    parser.add_argument("--base-url", help="OpenAI-compatible API endpoint")
    args = parser.parse_args()
    auth = "api_key"
    key_name = KEY_NAMES[args.provider or "openai"]
    generation = GenerationConfig(model=args.model or "normal")
    key = os.environ.get(key_name)
    if not args.model or not key:
        if not sys.stdin.isatty():
            parser.error(
                "Interactive setup needs a terminal; provide --model and the provider API key environment variable"
            )
        config = Setup(args.provider, args.model, args.base_url).run()
        if config is None:
            return
        args.provider, args.model, args.base_url = config.provider, config.model, config.base_url
        key = config.api_key
        generation = config.generation
        auth = config.auth_method
    args.provider = args.provider or "openai"
    if args.provider != "openai" and args.base_url:
        parser.error("--base-url requires --provider openai")
    if args.provider == "openai":
        provider = (
            CodexProvider(key)
            if auth == "oauth"
            else OpenAIProvider(key, **({"base_url": args.base_url} if args.base_url else {}))
        )
    elif args.provider == "anthropic":
        provider = AnthropicProvider(
            key, renew_credentials=renew_claude_access_token if auth == "oauth" else None
        )
    else:
        provider = GeminiOAuthProvider(key) if auth == "oauth" else GeminiProvider(key)
    Chat(SentinelProviderAdapter(provider), args.model, generation=generation).run()


if __name__ == "__main__":
    main()
