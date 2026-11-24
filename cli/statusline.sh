#!/usr/bin/env sh
# Antigravity CLI custom status line (POSIX sh + jq).
# Reads StatusLineData JSON from stdin and sums per-turn token usage + nominal
# API list-price USD from transcript.jsonl (preferring .cost.total_usd if set).

input=$(cat)
[ -z "$input" ] && exit 0

meta=$(printf '%s\n' "$input" | jq -r '
  (.transcript_path // "") as $tp |
  (.session_id // "") as $sid |
  (if $tp != "" then $tp
   elif ($sid | test("^[0-9a-fA-F-]{36}$")) then
     "\(env.HOME // "")/.gemini/antigravity-cli/brain/\($sid)/.system_generated/logs/transcript.jsonl"
   else "" end) as $main |
  ($main | if contains("/brain/") then (split("/brain/")[0] + "/brain") else "" end) as $brain |
  tojson,
  ([
    $main,
    ((.subagents // [])[]? |
      if (.transcript_path // "") != "" then .transcript_path
      elif (.log_uri // "") != "" then (.log_uri | sub("^file://"; ""))
      elif ($brain != "" and ((.session_id // .id // "") != "")) then
        "\($brain)/\(.session_id // .id)/.system_generated/logs/transcript.jsonl"
      else "" end)
  ] | unique | .[] | select(. != ""))
' 2>/dev/null) || exit 0
[ -z "$meta" ] && exit 0

compact=${meta%%
*}
if [ "$meta" != "$compact" ]; then
  tpaths=${meta#*
}
else
  tpaths=""
fi

set --
if [ -n "$tpaths" ]; then
  old_ifs=$IFS
  IFS='
'
  for p in $tpaths; do
    [ -r "$p" ] && set -- "$@" "$p"
  done
  IFS=$old_ifs
fi

{
  printf '%s\n' "$compact"
  if [ "$#" -gt 0 ]; then
    grep -h '"PLANNER_RESPONSE"' "$@" 2>/dev/null || true
  fi
} | jq -R -s -r '
  # Nominal API list prices in USD per 1M tokens: [uncached_in, cache_read, out]
  def rates($m; $prompt_tok):
    ($m | ascii_downcase) as $id |
    if ($id | startswith("claude-opus")) then
      if ($id | test("claude-opus-(4-[56]|5)")) then [5.0, 0.5, 25.0]
      else [15.0, 1.5, 75.0] end
    elif ($id | startswith("claude-sonnet")) then
      if $prompt_tok > 200000 then [6.0, 0.6, 22.5] else [3.0, 0.3, 15.0] end
    elif ($id | startswith("claude-haiku")) then
      [1.0, 0.1, 5.0]
    elif ($id | test("gemini-2(\\.|p)5-pro")) then
      if $prompt_tok > 200000 then [2.5, 0.25, 15.0] else [1.25, 0.125, 10.0] end
    elif ($id | test("gemini-2(\\.|p)5-flash-lite")) then
      [0.10, 0.01, 0.40]
    elif ($id | contains("flash-lite")) then
      [0.25, 0.025, 1.50]
    elif ($id | test("gemini-2(\\.|p)5-flash")) then
      [0.30, 0.03, 2.50]
    elif (($id | contains("flash")) or ($id | startswith("gemini-4-hydrogen"))) then
      [0.50, 0.05, 3.00]
    else
      # Gemini 3.x Pro and Gemini 4 orchestrators (neon, carbon, argon)
      if $prompt_tok > 200000 then [4.0, 0.40, 18.0] else [2.0, 0.20, 12.0] end
    end;

  def fmt_dec1($x):
    (($x * 10 | round) / 10 | tostring) |
    if contains(".") then . else . + ".0" end;

  def fmt_tok:
    if . < 1000 then "\(. | round)"
    elif . < 99950 then "\(fmt_dec1(. / 1000))k"
    elif . < 999500 then "\((. / 1000) | round)k"
    elif . < 99950000 then "\(fmt_dec1(. / 1000000))M"
    else "\((. / 1000000) | round)M"
    end;

  def fmt_usd($est):
    (if $est then "~$" else "$" end) as $p |
    if . <= 0 then "$0.00"
    elif . < 0.0095 then
      ((. * 1000 | round) | if . < 1 then 1 else . end) as $m |
      "\($p)0." + ("000" + ($m | tostring))[-3:]
    else
      (. * 100 | round) as $c |
      "\($p)\(($c / 100) | floor)." + ("00" + (($c % 100) | tostring))[-2:]
    end;

  split("\n") |
  (.[0] | fromjson? // {}) as $s |
  (.[1:] | map(fromjson? | select(type == "object" and .type == "PLANNER_RESPONSE"))) as $turns |
  ($s.model.id // $s.model.display_name // "gemini-pro") as $active_model |

  (if ($turns | length) > 0 then
    reduce $turns[] as $t (
      {tok: 0, usd: 0};
      ($t.input_tokens // 0) as $in |
      ($t.cache_read_tokens // 0) as $cr |
      ($t.output_tokens // 0) as $out |
      rates(($t.model // $active_model); ($in + $cr)) as $r |
      {
        tok: (.tok + $in + $cr + $out),
        usd: (.usd + (($in * $r[0] + $cr * $r[1] + $out * $r[2]) / 1000000))
      }
    )
  else
    (($s.context_window.total_input_tokens // 0) + ($s.context_window.total_output_tokens // 0)) as $ftok |
    ($s.context_window.total_input_tokens // 0) as $fin |
    ($s.context_window.total_output_tokens // 0) as $fout |
    rates($active_model; $fin) as $r |
    {
      tok: $ftok,
      usd: (($fin * $r[0] + $fout * $r[2]) / 1000000)
    }
  end) as $calc |

  (if (($s.cost.total_usd // 0) > 0) then
    {
      usd: $s.cost.total_usd,
      est: (if ($s.cost.estimated == false) then false else true end)
    }
  else
    {usd: $calc.usd, est: true}
  end) as $cost |

  "\u001b[0m" as $r |
  "\u001b[90m │ \u001b[0m" as $sep |
  ($s.model.display_name // "Gemini") as $model |
  ($s.cycle_mode // "auto") as $mode |
  (if ($s.vim.mode // "") != "" then " [\($s.vim.mode)]" else "" end) as $vim |
  ((if ($s.conversation_title // "") != "" then $s.conversation_title else ("#" + ($s.session_id // "new")[0:8]) end) | gsub("[\\r\\n]+"; " ")) as $title |
  (($s.context_window.used_percentage // 0) | round) as $pct |
  (if $pct >= 80 then "\u001b[91m" elif $pct >= 50 then "\u001b[93m" else "\u001b[92m" end) as $ctx_color |
  ($calc.tok | fmt_tok) as $tok_str |
  ($cost.usd | fmt_usd($cost.est)) as $usd_str |

  "\u001b[96m\($model)\($r)\($sep)\u001b[95m\($mode)\($vim)\($r)\($sep)cxt: \($ctx_color)\($pct)%\($r)\($sep)\($tok_str) (\u001b[33m\($usd_str)\($r))\($sep)\u001b[97m\($title)\($r)"
'
