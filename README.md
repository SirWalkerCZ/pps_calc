# Návrh přípojné sítě

Skript počítá vzdálenostní matici pro variantu C zadání, hledá minimální strom a jeden kruh nad vzdálenostmi pro pokládku optiky z Mapy.com API a generuje obrázky do složky `vystupy`.

## Spuštění

1. Vytvoř `.env` podle `.env.example`.
2. Doinstaluj závislosti:

```bash
pip install -r requirements.txt
```

3. Spusť skript:

```bash
python main.py
```

Výstupy:

- `vystupy/minimalni_strom.png`
- `vystupy/jeden_kruh.png`
- `vystupy/tab_vzdalenosti_auto.tex`
- `vystupy/tab_casy_auto.tex`
- `vystupy/tab_vzdalenosti_pokladka.tex`
- `vystupy/tab_minimalni_strom_pokladka.tex`
- `vystupy/tab_jeden_kruh_pokladka.tex`
- `vystupy/tab_vzdalenosti_vzdusne.tex`

Obrázky nemají vlastní nadpis, popisek patří až do LaTeXu:

```latex
\begin{figure}[htbp]
    \centering
    \includegraphics[width=\textwidth]{vystupy/minimalni_strom.png}
    \caption{Minimální strom}
\end{figure}
```

Tabulky jsou uložené jako fragmenty pro `\input`:

```latex
\begin{table}[htbp]
    \centering
    \input{vystupy/tab_vzdalenosti_pokladka.tex}
    \caption{Matice vzdáleností pro pokládku}
\end{table}
```
