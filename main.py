import itertools
import math
import os
import sys
from io import BytesIO
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
ROUTE_URL = "https://api.mapy.com/v1/routing/route"
EARTH_RADIUS_METERS = 6_371_000
OUTPUT_DIR = "vystupy"
MAP_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_ZOOM = 12
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

# Pro fyzickou pokládku optiky je možné u konkrétních hran uvažovat i cesty,
# cyklostezky a polní/lesní komunikace, ne jen automobilové silnice.
PHYSICAL_ROUTE_TYPE_OVERRIDES = {
    frozenset(("Mšec", "Srby")): "bike_mountain",
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


def route_type_for_edge(start_name: str, end_name: str, default: str = "car_fast") -> str:
    return PHYSICAL_ROUTE_TYPE_OVERRIDES.get(frozenset((start_name, end_name)), default)


def load_route(start: dict[str, Any], end: dict[str, Any], route_type: str) -> dict[str, Any]:
    data = api_get(
        ROUTE_URL,
        start=f"{start['lon']},{start['lat']}",
        end=f"{end['lon']},{end['lat']}",
        routeType=route_type,
        format="geojson",
    )
    coordinates = data["geometry"]["geometry"]["coordinates"]
    return {
        "length": float(data["length"]),
        "duration": float(data["duration"]),
        "routeType": route_type,
        "coordinates": [(float(lon), float(lat)) for lon, lat in coordinates],
    }


def load_route_geometry(start: dict[str, Any], end: dict[str, Any], route_type: str) -> list[tuple[float, float]]:
    return load_route(start, end, route_type)["coordinates"]


def build_physical_matrix(
    points: list[dict[str, Any]],
    names: list[str],
    car_matrix: list[list[dict[str, Any]]],
) -> list[list[dict[str, Any]]]:
    matrix = [[dict(entry) for entry in row] for row in car_matrix]

    for edge_names, route_type in PHYSICAL_ROUTE_TYPE_OVERRIDES.items():
        start_name, end_name = tuple(edge_names)
        start_index = names.index(start_name)
        end_index = names.index(end_name)
        route = load_route(points[start_index], points[end_index], route_type)
        for a, b in [(start_index, end_index), (end_index, start_index)]:
            matrix[a][b]["length"] = route["length"]
            matrix[a][b]["duration"] = route["duration"]
            matrix[a][b]["routeType"] = route_type

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


def print_route_type_overrides(names: list[str], matrix: list[list[dict[str, Any]]]) -> None:
    if not PHYSICAL_ROUTE_TYPE_OVERRIDES:
        return

    print("\nUpravené fyzické trasy pro pokládku")
    for edge_names, route_type in PHYSICAL_ROUTE_TYPE_OVERRIDES.items():
        start_name, end_name = tuple(edge_names)
        start_index = names.index(start_name)
        end_index = names.index(end_name)
        print(f"- {start_name} -- {end_name}: {format_km(entry_length(matrix[start_index][end_index]))} km ({route_type})")


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


def lon_to_tile_x(lon: float, zoom: int) -> int:
    return int((lon + 180.0) / 360.0 * (2**zoom))


def lat_to_tile_y(lat: float, zoom: int) -> int:
    lat_rad = math.radians(lat)
    return int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * (2**zoom))


def tile_x_to_lon(tile_x: int, zoom: int) -> float:
    return tile_x / (2**zoom) * 360.0 - 180.0


def tile_y_to_lat(tile_y: int, zoom: int) -> float:
    mercator = math.pi * (1 - 2 * tile_y / (2**zoom))
    return math.degrees(math.atan(math.sinh(mercator)))


def lon_lat_to_global_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    lat_rad = math.radians(lat)
    scale = 256 * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale
    return x, y


def load_map_background(
    coordinates: list[tuple[float, float]],
    zoom: int = MAP_ZOOM,
    padding_px: int = 512,
):
    from PIL import Image

    pixels = [lon_lat_to_global_pixel(lon, lat, zoom) for lon, lat in coordinates]
    min_pixel_x = min(x for x, _ in pixels) - padding_px
    max_pixel_x = max(x for x, _ in pixels) + padding_px
    min_pixel_y = min(y for _, y in pixels) - padding_px
    max_pixel_y = max(y for _, y in pixels) + padding_px

    min_x = math.floor(min_pixel_x / 256)
    max_x = math.floor(max_pixel_x / 256)
    min_y = math.floor(min_pixel_y / 256)
    max_y = math.floor(max_pixel_y / 256)

    width = (max_x - min_x + 1) * 256
    height = (max_y - min_y + 1) * 256
    background = Image.new("RGB", (width, height), "#f3efe8")

    headers = {"User-Agent": "pocitame-vzdalenost/1.0"}
    for tile_x in range(min_x, max_x + 1):
        for tile_y in range(min_y, max_y + 1):
            url = MAP_TILE_URL.format(z=zoom, x=tile_x, y=tile_y)
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            tile = Image.open(BytesIO(response.content)).convert("RGB")
            background.paste(tile, ((tile_x - min_x) * 256, (tile_y - min_y) * 256))

    origin = (min_x * 256, min_y * 256)
    return background, origin


def to_local_pixel(lon: float, lat: float, origin: tuple[int, int], zoom: int = MAP_ZOOM) -> tuple[float, float]:
    global_x, global_y = lon_lat_to_global_pixel(lon, lat, zoom)
    return global_x - origin[0], global_y - origin[1]


def route_midpoint(coordinates: list[tuple[float, float]]) -> tuple[float, float]:
    if not coordinates:
        return 0.0, 0.0
    return coordinates[len(coordinates) // 2]


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

    fig, ax = plt.subplots(figsize=(16, 11))

    route_geometries = {}
    all_coordinates = [(point["lon"], point["lat"]) for point in points]

    for start, end in edges:
        try:
            route_type = route_type_for_edge(points[start]["name"], points[end]["name"])
            coordinates = load_route_geometry(points[start], points[end], route_type)
        except (requests.RequestException, KeyError, ValueError) as exc:
            print(
                f"Varování: nepodařilo se načíst geometrii trasy {points[start]['name']} - {points[end]['name']} ({exc}).",
                file=sys.stderr,
            )
            coordinates = [
                (points[start]["lon"], points[start]["lat"]),
                (points[end]["lon"], points[end]["lat"]),
            ]
        route_geometries[(start, end)] = coordinates
        all_coordinates.extend(coordinates)

    map_loaded = False
    origin = (0, 0)

    try:
        background, origin = load_map_background(all_coordinates)
        ax.imshow(background, interpolation="bilinear", zorder=0)
        map_loaded = True
    except requests.RequestException as exc:
        print(f"Varování: nepodařilo se načíst mapový podklad ({exc}). Kreslím čistý graf.", file=sys.stderr)

    for start, end in edges:
        coordinates = route_geometries[(start, end)]
        route_pixels = [to_local_pixel(lon, lat, origin) for lon, lat in coordinates]
        xs = [x for x, _ in route_pixels]
        ys = [y for _, y in route_pixels]
        ax.plot(
            xs,
            ys,
            color="#1f77b4",
            linewidth=3.4,
            alpha=0.95,
            zorder=2,
        )

        label_lon, label_lat = route_midpoint(coordinates)
        label_x, label_y = to_local_pixel(label_lon, label_lat, origin)
        ax.text(
            label_x,
            label_y,
            f"{format_km(entry_length(matrix[start][end]))} km",
            fontsize=8,
            ha="center",
            va="center",
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": "#d9d9d9", "alpha": 0.9},
            zorder=4,
        )

    point_pixels = [to_local_pixel(point["lon"], point["lat"], origin) for point in points]
    point_xs = [x for x, _ in point_pixels]
    point_ys = [y for _, y in point_pixels]
    ax.scatter(point_xs, point_ys, s=145, color="#d62728", edgecolor="white", linewidth=1.7, zorder=5)
    for index, point in enumerate(points, start=1):
        point_x, point_y = to_local_pixel(point["lon"], point["lat"], origin)
        ax.annotate(
            f"{index}. {point['name']}",
            (point_x, point_y),
            xytext=(7, 7),
            textcoords="offset points",
            fontsize=10,
            weight="bold",
            bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.78},
            zorder=6,
        )

    all_pixels = [to_local_pixel(lon, lat, origin) for lon, lat in all_coordinates]
    min_x = min(x for x, _ in all_pixels)
    max_x = max(x for x, _ in all_pixels)
    min_y = min(y for _, y in all_pixels)
    max_y = max(y for _, y in all_pixels)
    padding_x = max((max_x - min_x) * 0.14, 140)
    padding_y = max((max_y - min_y) * 0.18, 140)
    ax.set_xlim(min_x - padding_x, max_x + padding_x)
    ax.set_ylim(max_y + padding_y, min_y - padding_y)
    ax.axis("off")
    if map_loaded:
        ax.text(
            0.01,
            0.01,
            "Map data © OpenStreetMap contributors",
            transform=ax.transAxes,
            fontsize=8,
            color="#333333",
            bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "none", "alpha": 0.75},
            zorder=7,
        )
    fig.tight_layout()
    fig.savefig(output_path, dpi=240)
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
    physical_matrix = build_physical_matrix(points, names, matrix)

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
    print_route_type_overrides(names, physical_matrix)
    physical_distance_table = save_latex_matrix_table(
        "tab_vzdalenosti_pokladka.tex",
        names,
        physical_matrix,
        lambda entry: format_km(entry_length(entry)),
    )

    tree_edges, tree_distance = find_minimum_spanning_tree(physical_matrix)
    tree_image = save_graph_image(
        "minimalni_strom.png",
        points,
        tree_edges,
        physical_matrix,
    )
    tree_table = save_latex_edges_table("tab_minimalni_strom_pokladka.tex", names, tree_edges, physical_matrix)
    print("\nMinimální strom pro pokládku")
    for start, end in tree_edges:
        length = entry_length(physical_matrix[start][end])
        print(f"- {names[start]} -- {names[end]}: {format_km(length)} km")
    print(f"Celkem: {format_km(tree_distance)} km")
    print(f"Obrázek: {tree_image}")

    route, distance = find_shortest_route(physical_matrix, start_index=0, return_to_start=False)
    route_names = " -> ".join(names[index] for index in route)
    print("\nNejkratší trasa pro pokládku přes všechna místa")
    print(f"{route_names}")
    print(f"Celkem: {format_km(distance)} km")

    route, distance = find_shortest_route(physical_matrix, start_index=0, return_to_start=True)
    route_names = " -> ".join(names[index] for index in route)
    circle_edges = route_to_edges(route, return_to_start=True)
    circle_image = save_graph_image(
        "jeden_kruh.png",
        points,
        circle_edges,
        physical_matrix,
    )
    circle_table = save_latex_edges_table("tab_jeden_kruh_pokladka.tex", names, circle_edges, physical_matrix)
    print("\nNejkratší okruh pro pokládku přes všechna místa")
    print(f"{route_names} -> {names[route[0]]}")
    print(f"Celkem: {format_km(distance)} km")
    print(f"Obrázek: {circle_image}")

    print("\nLaTeX tabulky")
    for table_path in [straight_table, car_distance_table, car_time_table, physical_distance_table, tree_table, circle_table]:
        print(f"- {table_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
