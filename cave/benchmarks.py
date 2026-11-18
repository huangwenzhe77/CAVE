"""Native ALFWorld API and ExpeL-compatible WebShop browser actions."""

from copy import deepcopy
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import math
import re
from urllib.parse import quote

from .runtime import Observation
from .schema import text


def _pin_file(config, path_key, hash_key):
    actual = sha256(Path(config[path_key]).read_bytes()).hexdigest()
    if hash_key in config and config[hash_key] != actual:
        raise ValueError(f"Pinned file changed: {path_key}")
    config[hash_key] = actual


class AlfworldEnvironment:
    restoration = "observational"

    def __init__(self, config):
        try:
            import alfworld.agents.environment as environments
            import yaml
        except ImportError as exc:
            raise RuntimeError("Install CAVE's alfworld extra and the ALFWorld dataset first") from exc
        self.metadata = deepcopy(config)
        _pin_file(self.metadata, "config_path", "config_sha256")
        _pin_file(self.metadata, "gamefile", "gamefile_sha256")
        installed = version("alfworld")
        if config.get("package_version", installed) != installed:
            raise ValueError("ALFWorld package version changed")
        self.metadata["package_version"] = installed
        with Path(config["config_path"]).open(encoding="utf-8") as stream:
            settings = yaml.safe_load(stream)
        resource_root = Path(config.get("resource_root", Path.cwd())).resolve()

        def resolve_resources(value):
            if isinstance(value, dict):
                return {key: resolve_resources(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve_resources(item) for item in value]
            if isinstance(value, str) and value.startswith("data/"):
                return str(resource_root / value)
            return value

        settings = resolve_resources(settings)
        split = config.get("split", settings.get("split", "eval_out_of_distribution"))
        path_key = {
            "train": "data_path",
            "eval_in_distribution": "eval_id_data_path",
            "eval_out_of_distribution": "eval_ood_data_path",
        }.get(split)
        if path_key is None:
            raise ValueError(f"Unsupported ALFWorld split: {split}")
        gamefile = Path(config["gamefile"]).resolve()
        split_root = Path(settings["dataset"][path_key]).resolve()
        if not gamefile.is_relative_to(split_root):
            raise ValueError("ALFWorld gamefile is outside the declared split")
        # This adapter runs one pinned game. Avoid scanning the entire split in
        # the upstream constructor before assigning the selected gamefile.
        settings["dataset"][path_key] = str(gamefile.parent)
        self.main = environments.get_environment(settings["env"]["type"])(
            settings, train_eval=split)
        self.main.game_files = [str(gamefile)]
        self.max_steps = int(config.get("max_steps", 50))
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.env = None
        self.last_action = None
        self.calls = 0

    def reset(self, seed):
        self.close()
        self.env = self.main.init_env(batch_size=1)
        self.env.seed(seed)
        observations, info = self.env.reset()
        self.calls, self.last_action = 0, None
        return self._observation(observations[0], info)

    def _observation(self, value, info, success=False, terminated=False, truncated=False, reason=None):
        raw = value
        if value.startswith("You arrive at loc "):
            value = value.partition(". ")[2]
        value = value.replace(" In it, you see nothing.", "").replace(", you see nothing", "")
        facts = {"admissible_commands": info.get("admissible_commands", [[]])[0],
                 "environment_steps": self.calls, "last_action": self.last_action}
        return Observation(value, facts, success, terminated, truncated, float(success), reason, raw)

    def step(self, action):
        # Match ExpeL's textual put syntax normalization.
        action = re.sub(r"^(put \w+\s*\d+) (?:in|on) (\w+\s*\d+)$", r"\1 in/on \2", action)
        observations, _, done, info = self.env.step([action])
        success = bool(info["won"][0])
        self.calls += 1
        repeated = action == self.last_action
        truncated = (self.calls >= self.max_steps and not success) or repeated
        self.last_action = action
        reason = ("environment_success" if success else "repeated_action" if repeated else
                  "step_budget_exhausted" if truncated else "environment_terminated" if done[0] else None)
        return self._observation(observations[0], info, success, bool(done[0]) or success or repeated,
                                 truncated, reason)

    def state_token(self):
        # Standard ALFWorld reset does not expose a complete serializable world state.
        return None

    def close(self):
        if self.env is not None:
            self.env.close()
            self.env = None


class WebshopEnvironment:
    restoration = "observational"
    subpages = {"Description": "description_page.html", "Features": "features_page.html",
                "Reviews": "review_page.html", "Attributes": "attributes_page.html"}

    def __init__(self, config):
        try:
            import requests
            from bs4 import BeautifulSoup, Comment
        except ImportError as exc:
            raise RuntimeError("Install CAVE's webshop extra first") from exc
        self.requests, self.soup, self.comment = requests, BeautifulSoup, Comment
        self.metadata = deepcopy(config)
        self.url = text(config["base_url"], "base_url").rstrip("/")
        self.session_id = text(str(config["session_idx"]), "session_idx")
        text(config["dataset_revision"], "dataset_revision")
        self.max_steps = int(config.get("max_steps", 15))
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.timeout = float(config.get("timeout_seconds", 30))
        self.session = {}
        self.calls, self.last_action = 0, None

    def _page(self):
        state = self.session
        sid = quote(self.session_id, safe="")
        query = quote(state.get("query", ""), safe="")
        asin = quote(state.get("asin", ""), safe="")
        options = quote(str(state.get("options", {})), safe="")
        page = state.get("page", 1)
        kind = state["page_type"]
        paths = {"init": sid, "search": f"search_results/{sid}/{query}/{page}",
                 "item": f"item_page/{sid}/{asin}/{query}/{page}/{options}",
                 "item_sub": f"item_sub_page/{sid}/{asin}/{query}/{page}/{state.get('subpage', '')}/{options}",
                 "end": f"done/{sid}/{asin}/{options}"}
        response = self.requests.get(self.url + "/" + paths[kind], timeout=self.timeout)
        response.raise_for_status()
        self.last_page = response.text
        dom = self.soup(response.text, "html.parser")
        nodes = [node for node in dom.find_all(string=True)
                 if node.parent.name not in {"style", "script", "head", "title", "meta", "[document]"}
                 and not isinstance(node, self.comment) and str(node).strip()]
        parts, option_types, asins, category = [], {}, [], ""
        for node in nodes:
            value = str(node).strip()
            if node.parent.name == "button":
                parts.append(f"[{value}]")
            elif node.parent.name == "label":
                option_types[value] = category
                selected = value in state.get("options", {}).values()
                parts.append(f"[[{value}]]" if selected else f"[{value}]")
            elif "product-link" in node.parent.get("class", []):
                asins.append(value)
                parts.append(f"[{value}]")
            else:
                parts.append(value)
                category = value
        if option_types:
            state["option_types"] = option_types
        if asins:
            state["asins"] = asins
        rendered = "\n".join(parts)
        result = re.search(r"Total results:\s*(\d+)", rendered)
        if result:
            state["max_products"] = int(result.group(1))
        score = 0.0
        score_match = re.search(r"Your score \(min 0\.0, max 1\.0\)\s*:?\s*([0-9.]+)", rendered)
        if score_match:
            score = float(score_match.group(1))
            if not 0 <= score <= 1:
                raise ValueError("WebShop score must be in [0,1]")
            rendered = "Your score (min 0.0, max 1.0): " + score_match.group(1)
        elif kind == "end":
            raise ValueError("WebShop done page has no parseable score")
        rendered = rendered.replace("WebShop\n", "").replace("Instruction:\n", "")
        return rendered, score

    def reset(self, seed):
        # The standard server owns hidden session state; seed is not a snapshot API.
        self.session = {"page_type": "init"}
        self.calls, self.last_action = 0, None
        value, _ = self._page()
        return Observation(value, {"session": deepcopy(self.session), "environment_steps": self.calls,
                                   "last_action": self.last_action}, raw_text=self.last_page)

    def step(self, action):
        state = self.session
        before = deepcopy(state)
        override = None
        try:
            if action == "reset":
                self.session = {"page_type": "init"}
            elif action.startswith("think[") and action.endswith("]"):
                override = "OK."
            elif action.startswith("search[") and action.endswith("]"):
                if state["page_type"] != "init":
                    raise ValueError("search outside init")
                self.session = {"page_type": "search", "query": action[7:-1], "page": 1}
            elif action.startswith("click[") and action.endswith("]"):
                button, kind = action[6:-1], state["page_type"]
                if button == "Buy Now" and kind == "item":
                    state["page_type"] = "end"
                elif button == "Back to Search" and kind in {"search", "item", "item_sub"}:
                    self.session = {"page_type": "init"}
                elif button == "Next >" and kind == "search" and state["page"] < math.ceil(state.get("max_products", 0) / 10):
                    state["page"] += 1
                elif button == "< Prev" and kind in {"search", "item", "item_sub"}:
                    if kind == "search":
                        if state["page"] <= 1:
                            raise ValueError("No previous page")
                        state["page"] -= 1
                    else:
                        state["page_type"] = "item" if kind == "item_sub" else "search"
                        if kind == "item":
                            state["options"] = {}
                elif button in self.subpages and kind == "item":
                    state.update(page_type="item_sub", subpage=self.subpages[button])
                elif kind == "search" and button in state.get("asins", []):
                    state.update(page_type="item", asin=button, options={})
                elif kind == "item" and button in state.get("option_types", {}):
                    state.setdefault("options", {})[state["option_types"][button]] = button
                    override = f"You have clicked {button}."
                else:
                    raise ValueError("Unknown click target")
            else:
                raise ValueError("Unknown action")
        except ValueError:
            self.session = before
            override = "Invalid action!"
        value, score = self._page()
        self.calls += 1
        repeated = override == "Invalid action!" and self.last_action is not None and any(
            action.lower().startswith(prefix) and self.last_action.lower().startswith(prefix)
            for prefix in ("search[", "think[i apologize", "think[end", "think[i'm sorry", "think[apolog"))
        if repeated:
            override = "Repeated action!"
        value = override or value
        terminated = self.session["page_type"] == "end" or repeated
        success = terminated and score == 1.0
        truncated = repeated or (self.calls >= self.max_steps and not terminated)
        if truncated and not terminated:
            value += "\n\nRan out of steps! TASK FAILED"
        self.last_action = action
        reason = ("environment_success" if success else "repeated_action" if repeated else
                  "purchase_score_below_one" if terminated else "step_budget_exhausted" if truncated else None)
        return Observation(value, {"session": deepcopy(self.session), "environment_steps": self.calls,
                                   "last_action": self.last_action}, success, terminated, truncated, score,
                           reason, self.last_page)

    def state_token(self):
        return None

    def close(self):
        pass


def create_environment(benchmark, config):
    if benchmark == "alfworld":
        return AlfworldEnvironment(config)
    if benchmark == "webshop":
        return WebshopEnvironment(config)
    raise ValueError(f"Unsupported benchmark: {benchmark}")
