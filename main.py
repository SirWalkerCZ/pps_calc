import itertools
import math
import os
import sys
from typing import Any

import requests


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

API_KEY = os.getenv("MAPY_API_KEY")
GEOCODE_URL = "https://api.mapy.com/v1/geocode"
EARTH_RADIUS_METERS = 6_371_000
OUTPUT_DIR = "vystupy"
MATRIX_URLS = [
    "https://api.mapy.com/v1/routing/matrix-m",
    "https://api.mapy.cz/v1/routing/matrix-m",
]

PLACES = [
    "Řevničov",
    "Mšec",
    "Kroučová",
    "Srby",
    "Mutějovice",
    "Krušovice",
    "Hředle",
    "Krupá",
    "Milý",
]

GEOCODE_QUERY_OVERRIDES = {
    "Srby": "Srby u Tuchlovic",
    "Hředle": "Hředle u Rakovníka",
}


def api_get(url: str, **params: Any) -> dict[str, Any]:
    response = requests.get(
        url,
        params={**params, "apikey": API_KEY},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def geocode(place: str) -> dict[str, Any]:
    query = GEOCODE_QUERY_OVERRIDES.get(place, place)
    data = api_get(GEOCODE_URL, query=query, lang="cs")
    items = data.get("items", [])
    if not items:
        raise ValueError(f"Nepodařilo se najít místo: {place}")

    item = items[0]
    position = item["position"]
    return {
        "name": place,
        "label": item.get("name") or item.get("label") or place,
        "lat": position["lat"],
        "lon": position["lon"],
    }


def load_matrix(points: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    coordinates = [f"{p['lon']},{p['lat']}" for p in points]
    params = {
        "starts": coordinates,
        "ends": coordinates,
        "routeType": "car_fast",
        "lang": "cs",
    }

    errors = []
    data = None

    for url in MATRIX_URLS:
        try:
            data = api_get(url, **params)
            break
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "?"
            response_text = exc.response.text if exc.response is not None else ""
            errors.append(f"{url}: {status_code} {response_text}")
            if status_code != 404:
                raise

    if data is None:
        raise ValueError("Nepodařilo se najít funkční endpoint pro maticové plánování:\n" + "\n".join(errors))

    # Mapy API vrací buď přímo 2D pole, nebo objekt s klíčem "matrix"
    # podle použité verze dokumentace/klienta.
    matrix = data.get("matrix") if isinstance(data, dict) else data
    if not isinstance(matrix, list):
        raise ValueError(f"Neočekávaná odpověď maticového plánování: {data}")

    return matrix


def entry_length(entry: dict[str, Any]) -> float:
    return float(entry["length"])


def entry_duration(entry: dict[str, Any]) -> float:
    return float(entry["duration"])


def straight_distance(point_a: dict[str, Any], point_b: dict[str, Any]) -> float:
    lat_a = math.radians(point_a["lat"])
    lon_a = math.radians(point_a["lon"])
    lat_b = math.radians(point_b["lat"])
    lon_b = math.radians(point_b["lon"])

    delta_lat = lat_b - lat_a
    delta_lon = lon_b - lon_a

    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    central_angle = 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    return EARTH_RADIUS_METERS * central_angle


def build_straight_matrix(points: list[dict[str, Any]]) -> list[list[dict[str, float]]]:
    return [
        [{"length": straight_distance(start, end)} for end in points]
        for start in points
    ]


def format_km(meters: float) -> str:
    return f"{meters / 1000:.1f}"


def format_minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f}"


def print_table(
    title: str,
    places: list[str],
    matrix: list[list[dict[str, Any]]],
    value_fn,
) -> None:
    first_col_width = max(4, len(str(len(places)))) + 2
    col_width = 7

    print(f"\n{title}")
    print(" " * first_col_width + "".join(str(index).rjust(col_width) for index in range(1, len(places) + 1)))

    for index, row in enumerate(matrix, start=1):
        values = [value_fn(entry).rjust(col_width) for entry in row]
        print(str(index).ljust(first_col_width) + "".join(values))

    print("\nLegenda")
    for index, place in enumerate(places, start=1):
        print(f"{index}: {place}")


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def save_latex_matrix_table(
    filename: str,
    places: list[str],
    matrix: list[list[dict[str, Any]]],
    value_fn,
) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, filename)
    columns = "r" + "r" * len(places)

    lines = [
        rf"\begin{{tabular}}{{{columns}}}",
        r"\hline",
        "Uzly & " + " & ".join(str(index) for index in range(1, len(places) + 1)) + r" \\",
        r"\hline",
    ]

    for index, row in enumerate(matrix, start=1):
        values = [value_fn(entry) for entry in row]
        lines.append(f"{index} & " + " & ".join(values) + r" \\")

    lines.extend([
        r"\hline",
        r"\end{tabular}",
        "",
        r"\begin{tabular}{rl}",
        r"\hline",
        r"Č. & Lokalita \\",
        r"\hline",
    ])

    for index, place in enumerate(places, start=1):
        lines.append(f"{index} & {latex_escape(place)}" + r" \\")

    lines.extend([
        r"\hline",
        r"\end{tabular}",
        "",
    ])

    with open(output_path, "w", encoding="utf-8") as table_file:
        table_file.write("\n".join(lines))

    return output_path


def save_latex_edges_table(
    filename: str,
    places: list[str],
    edges: list[tuple[int, int]],
    matrix: list[list[dict[str, Any]]],
) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, filename)
    lines = [
        r"\begin{tabular}{rllr}",
        r"\hline",
        r"Č. & Z uzlu & Do uzlu & Délka [km] \\",
        r"\hline",
    ]

    for index, (start, end) in enumerate(edges, start=1):
        start_name = latex_escape(places[start])
        end_name = latex_escape(places[end])
        length = format_km(entry_length(matrix[start][end]))
        lines.append(f"{index} & {start_name} & {end_name} & {length}" + r" \\")

    total_length = sum(entry_length(matrix[start][end]) for start, end in edges)
    lines.extend([
        r"\hline",
        rf"\multicolumn{{3}}{{r}}{{Celkem}} & {format_km(total_length)} \\",
        r"\hline",
        r"\end{tabular}",
        "",
    ])

    with open(output_path, "w", encoding="utf-8") as table_file:
        table_file.write("\n".join(lines))

    return output_path


def route_distance(route: tuple[int, ...], matrix: list[list[dict[str, Any]]], return_to_start: bool) -> float:
    total = 0.0
    for start, end in zip(route, route[1:]):
        total += entry_length(matrix[start][end])

    if return_to_start and len(route) > 1:
        total += entry_length(matrix[route[-1]][route[0]])

    return total


def find_shortest_route(
    matrix: list[list[dict[str, Any]]],
    start_index: int = 0,
    return_to_start: bool = False,
) -> tuple[tuple[int, ...], float]:
    indexes = [i for i in range(len(matrix)) if i != start_index]
    best_route: tuple[int, ...] | None = None
    best_distance = float("inf")

    for order in itertools.permutations(indexes):
        route = (start_index, *order)
        distance = route_distance(route, matrix, return_to_start)
        if distance < best_distance:
            best_route = route
            best_distance = distance

    if best_route is None:
        return (start_index,), 0.0

    return best_route, best_distance


def find_minimum_spanning_tree(matrix: list[list[dict[str, Any]]]) -> tuple[list[tuple[int, int]], float]:
    edges = []
    for start in range(len(matrix)):
        for end in range(start + 1, len(matrix)):
            edges.append((entry_length(matrix[start][end]), start, end))

    parent = list(range(len(matrix)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> bool:
        root_first = find(first)
        root_second = find(second)
        if root_first == root_second:
            return False
        parent[root_second] = root_first
        return True

    selected_edges = []
    total_length = 0.0
    for length, start, end in sorted(edges):
        if union(start, end):
            selected_edges.append((start, end))
            total_length += length
            if len(selected_edges) == len(matrix) - 1:
                break

    return selected_edges, total_length


def route_to_edges(route: tuple[int, ...], return_to_start: bool = False) -> list[tuple[int, int]]:
    edges = list(zip(route, route[1:]))
    if return_to_start and len(route) > 1:
        edges.append((route[-1], route[0]))
    return edges


def save_graph_image(
    filename: str,
    points: list[dict[str, Any]],
    edges: list[tuple[int, int]],
    matrix: list[list[dict[str, Any]]],
) -> str:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, filename)

    fig, ax = plt.subplots(figsize=(12, 8))

    lons = [point["lon"] for point in points]
    lats = [point["lat"] for point in points]

    for start, end in edges:
        point_a = points[start]
        point_b = points[end]
        ax.plot(
            [point_a["lon"], point_b["lon"]],
            [point_a["lat"], point_b["lat"]],
            color="#1f77b4",
            linewidth=2.8,
            zorder=1,
        )

        label_lon = (point_a["lon"] + point_b["lon"]) / 2
        label_lat = (point_a["lat"] + point_b["lat"]) / 2
        ax.text(
            label_lon,
            label_lat,
            f"{format_km(entry_length(matrix[start][end]))} km",
            fontsize=8,
            ha="center",
            va="center",
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": "#d9d9d9", "alpha": 0.9},
            zorder=3,
        )

    ax.scatter(lons, lats, s=140, color="#d62728", edgecolor="white", linewidth=1.5, zorder=4)
    for index, point in enumerate(points, start=1):
        ax.annotate(
            f"{index}. {point['name']}",
            (point["lon"], point["lat"]),
            xytext=(7, 7),
            textcoords="offset points",
            fontsize=10,
            weight="bold",
            zorder=5,
        )

    ax.set_xlabel("zeměpisná délka")
    ax.set_ylabel("zeměpisná šířka")
    ax.grid(True, color="#e6e6e6", linewidth=0.8)
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)

    return output_path


def print_closest_pairs(
    title: str,
    places: list[str],
    matrix: list[list[dict[str, Any]]],
    limit: int = 10,
    show_duration: bool = False,
) -> None:
    pairs = []
    for i, start in enumerate(places):
        for j, end in enumerate(places):
            if i == j:
                continue
            duration = entry_duration(matrix[i][j]) if show_duration else None
            pairs.append((entry_length(matrix[i][j]), duration, start, end))

    print(f"\n{title}")
    for length, duration, start, end in sorted(pairs)[:limit]:
        if duration is None:
            print(f"- {start} -> {end}: {format_km(length)} km")
        else:
            print(f"- {start} -> {end}: {format_km(length)} km, {format_minutes(duration)} min")


def main() -> int:
    if not API_KEY:
        print("Chybí MAPY_API_KEY. Vytvoř soubor .env podle .env.example.", file=sys.stderr)
        return 1

    try:
        points = [geocode(place) for place in PLACES]
        matrix = load_matrix(points)
    except requests.HTTPError as exc:
        response_text = exc.response.text if exc.response is not None else ""
        print(f"Chyba API: {exc}\n{response_text}", file=sys.stderr)
        return 1
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        return 1

    names = [point["name"] for point in points]
    straight_matrix = build_straight_matrix(points)

    print("Nalezená místa")
    for point in points:
        print(f"- {point['name']}: {point['lat']:.6f}, {point['lon']:.6f}")

    print_table("Vzdálenosti vzdušnou čarou v km", names, straight_matrix, lambda entry: format_km(entry_length(entry)))
    print_closest_pairs("10 nejkratších vzdáleností vzdušnou čarou", names, straight_matrix)
    straight_table = save_latex_matrix_table(
        "tab_vzdalenosti_vzdusne.tex",
        names,
        straight_matrix,
        lambda entry: format_km(entry_length(entry)),
    )

    route, distance = find_shortest_route(straight_matrix, start_index=0, return_to_start=False)
    route_names = " -> ".join(names[index] for index in route)
    print("\nNejkratší trasa vzdušnou čarou přes všechna místa")
    print(f"{route_names}")
    print(f"Celkem: {format_km(distance)} km")

    route, distance = find_shortest_route(straight_matrix, start_index=0, return_to_start=True)
    route_names = " -> ".join(names[index] for index in route)
    print("\nNejkratší okruh vzdušnou čarou přes všechna místa")
    print(f"{route_names} -> {names[route[0]]}")
    print(f"Celkem: {format_km(distance)} km")

    print_table("Vzdálenosti autem v km", names, matrix, lambda entry: format_km(entry_length(entry)))
    print_table("Časy autem v minutách", names, matrix, lambda entry: format_minutes(entry_duration(entry)))
    print_closest_pairs("10 nejkratších přejezdů autem", names, matrix, show_duration=True)
    car_distance_table = save_latex_matrix_table(
        "tab_vzdalenosti_auto.tex",
        names,
        matrix,
        lambda entry: format_km(entry_length(entry)),
    )
    car_time_table = save_latex_matrix_table(
        "tab_casy_auto.tex",
        names,
        matrix,
        lambda entry: format_minutes(entry_duration(entry)),
    )

    tree_edges, tree_distance = find_minimum_spanning_tree(matrix)
    tree_image = save_graph_image(
        "minimalni_strom.png",
        points,
        tree_edges,
        matrix,
    )
    tree_table = save_latex_edges_table("tab_minimalni_strom_auto.tex", names, tree_edges, matrix)
    print("\nMinimální strom autem")
    for start, end in tree_edges:
        length = entry_length(matrix[start][end])
        print(f"- {names[start]} -- {names[end]}: {format_km(length)} km")
    print(f"Celkem: {format_km(tree_distance)} km")
    print(f"Obrázek: {tree_image}")

    route, distance = find_shortest_route(matrix, start_index=0, return_to_start=False)
    route_names = " -> ".join(names[index] for index in route)
    print("\nNejkratší trasa autem přes všechna místa")
    print(f"{route_names}")
    print(f"Celkem: {format_km(distance)} km")

    route, distance = find_shortest_route(matrix, start_index=0, return_to_start=True)
    route_names = " -> ".join(names[index] for index in route)
    circle_edges = route_to_edges(route, return_to_start=True)
    circle_image = save_graph_image(
        "jeden_kruh.png",
        points,
        circle_edges,
        matrix,
    )
    circle_table = save_latex_edges_table("tab_jeden_kruh_auto.tex", names, circle_edges, matrix)
    print("\nNejkratší okruh autem přes všechna místa")
    print(f"{route_names} -> {names[route[0]]}")
    print(f"Celkem: {format_km(distance)} km")
    print(f"Obrázek: {circle_image}")

    print("\nLaTeX tabulky")
    for table_path in [straight_table, car_distance_table, car_time_table, tree_table, circle_table]:
        print(f"- {table_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
