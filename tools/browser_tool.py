import os
import time
import asyncio
import threading
import urllib.parse
import subprocess
from config import config
from tools.base import BaseTool, ToolResult

# Robust LangChain LLM subclasses for browser-use 0.13.7 compatibility with 429 rate limit backoff
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

    class BrowserUseGemini(ChatGoogleGenerativeAI):
        provider: str = "google"
        
        @property
        def model_name(self) -> str:
            return getattr(self, "model", "gemini-2.0-flash")

        def _clean_content(self, content):
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                new_list = []
                for part in content:
                    if hasattr(part, 'text'):
                        new_list.append({'type': 'text', 'text': part.text})
                    elif hasattr(part, 'image'):
                        img_data = part.image
                        if hasattr(img_data, 'base64'):
                            b64 = img_data.base64
                            media_type = getattr(img_data, 'media_type', 'image/png')
                            new_list.append({'type': 'image_url', 'image_url': {'url': f'data:{media_type};base64,{b64}'}})
                        elif isinstance(img_data, str):
                            new_list.append({'type': 'image_url', 'image_url': {'url': img_data}})
                        else:
                            new_list.append({'type': 'text', 'text': str(part)})
                    elif isinstance(part, (dict, str)):
                        new_list.append(part)
                    else:
                        new_list.append({'type': 'text', 'text': str(part)})
                return new_list
            return str(content)

        def _convert_messages(self, messages):
            if not isinstance(messages, list):
                return messages
            converted = []
            for msg in messages:
                if hasattr(msg, 'role') or hasattr(msg, 'content'):
                    role = str(getattr(msg, 'role', getattr(msg, 'type', 'user'))).lower()
                    raw_content = getattr(msg, 'content', str(msg))
                    content = self._clean_content(raw_content)
                    if 'system' in role:
                        converted.append(SystemMessage(content=content))
                    elif 'ai' in role or 'assistant' in role:
                        converted.append(AIMessage(content=content))
                    else:
                        converted.append(HumanMessage(content=content))
                else:
                    converted.append(msg)
            return converted

        async def ainvoke(self, input, config=None, **kwargs):
            if config is not None and not isinstance(config, dict):
                config = None
            kwargs.pop('output_format', None)
            kwargs.pop('session_id', None)
            input = self._convert_messages(input)
            
            # Retry loop with backoff for 429 rate limits on free tier
            for attempt in range(3):
                try:
                    return await super().ainvoke(input, config=config, **kwargs)
                except Exception as e:
                    if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                        await asyncio.sleep(2.5 * (attempt + 1))
                    else:
                        raise e
            return await super().ainvoke(input, config=config, **kwargs)

        def invoke(self, input, config=None, **kwargs):
            if config is not None and not isinstance(config, dict):
                config = None
            kwargs.pop('output_format', None)
            kwargs.pop('session_id', None)
            input = self._convert_messages(input)
            
            for attempt in range(3):
                try:
                    return super().invoke(input, config=config, **kwargs)
                except Exception as e:
                    if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                        time.sleep(2.5 * (attempt + 1))
                    else:
                        raise e
            return super().invoke(input, config=config, **kwargs)

except Exception:
    BrowserUseGemini = None

try:
    from langchain_openai import ChatOpenAI
    class BrowserUseOpenAI(ChatOpenAI):
        provider: str = "openai"
except Exception:
    BrowserUseOpenAI = None

class BrowserTool(BaseTool):
    name = "browser"
    description = "Steuert den Browser autonom mittels des KI-Frameworks 'browser-use' (Playwright + LLM Vision & Action Planner)."

    def run_browser_use_agent(self, task_prompt: str) -> str:
        api_key = config.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        openai_key = config.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")

        def _worker():
            try:
                from browser_use import Agent
                
                # Choose LLM for browser-use (Gemini or OpenAI)
                llm = None
                if api_key and api_key != "your_gemini_api_key_here" and BrowserUseGemini:
                    llm = BrowserUseGemini(model="gemini-2.0-flash", google_api_key=api_key)
                elif openai_key and openai_key != "your_openai_api_key_here" and BrowserUseOpenAI:
                    llm = BrowserUseOpenAI(model="gpt-4o-mini", api_key=openai_key)

                if not llm:
                    print("[browser-use]: Keiner der API-Keys (Gemini/OpenAI) gefunden.")
                    return

                # Run browser-use agent with use_vision=False to reduce token size by 95% and avoid free-tier 429 limits
                agent = Agent(
                    task=task_prompt,
                    llm=llm,
                    use_vision=False
                )
                asyncio.run(agent.run())
            except Exception as e:
                print(f"[browser-use Fehler]: {e}")

        # Execute browser-use in background thread so CLI stays responsive
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        return f"🤖 [browser-use KI-Agent gestartet]\nAufgabe: '{task_prompt}'\nDer KI-Agent übernimmt jetzt den Browser und führt Klicks, Suchen & Warenkorb-Aktionen autonom durch!"

    def play_youtube(self, query: str = "Katzen") -> ToolResult:
        clean_q = query.replace("Spiele", "").replace("spiele", "").replace("ein YouTube-Video", "").replace("YouTube Video", "").replace("YouTube", "").replace("youtube", "").replace("video", "").replace("ab", "").replace("zu", "").replace("über", "").strip()
        search_term = clean_q or "Katzen"
        encoded = urllib.parse.quote(search_term)
        url = f"https://www.youtube.com/results?search_query={encoded}"
        subprocess.run(["open", url], check=False)
        return ToolResult(
            success=True,
            output=f"🎬 YouTube für '{search_term}' auf deinem Mac geöffnet!"
        )

    def execute(self, action: str = "pizza", **kwargs) -> ToolResult:
        if action in ("youtube", "video", "play_video"):
            q = kwargs.get("query", kwargs.get("q", "Katzen"))
            return self.play_youtube(query=q)
        elif action in ("pizza", "order_pizza", "bestellen", "agent"):
            pizza = kwargs.get("pizza", kwargs.get("query", "Pizza Salami"))
            task = f"Öffne Lieferando.de, gebe die Adresse ein, suche nach {pizza}, wähle ein Restaurant mit guten Bewertungen, lege eine {pizza} in den Warenkorb und gehe bis zum Kassen-Bildschirm."
            res_msg = self.run_browser_use_agent(task)
            return ToolResult(success=True, output=res_msg)
        else:
            prompt = kwargs.get("query", "Öffne den Browser und navigiere zu Google.")
            res_msg = self.run_browser_use_agent(prompt)
            return ToolResult(success=True, output=res_msg)
