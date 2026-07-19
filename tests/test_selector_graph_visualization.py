"""
画图 把 selector graph 渲染成 PNG 图片
"""
import os
import sys
from pathlib import Path

# Add the project root directory to the Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tradingagents.graph.selector.selector_graph import AiSelectorGraph


DEFAULT_OUTPUT = Path(__file__).with_name("selector_graph_xray.png")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def render_selector_graph_png(output_path: Path | None = None, *, xray: bool = True) -> Path:
    """Compile the selector graph and render it as a Mermaid PNG."""
    graph = AiSelectorGraph(config={}, quick_llm=object(), deep_llm=object()).create_graph()
    png_bytes = graph.get_graph(xray=xray).draw_mermaid_png()

    target_path = Path(output_path) if output_path else DEFAULT_OUTPUT
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(png_bytes)
    return target_path


def test_render_selector_graph_png_creates_valid_png(tmp_path):
    output_path = "./selector_graph_xray.png"
    result_path = render_selector_graph_png(output_path)

    data = result_path.read_bytes()
    assert result_path.exists()
    assert len(data) > len(PNG_SIGNATURE)
    assert data.startswith(PNG_SIGNATURE)


if __name__ == "__main__":
    output_path = render_selector_graph_png()
    print(output_path.resolve())

