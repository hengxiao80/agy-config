#!/usr/bin/env sh
exec jq -r '
  "\u001b[0m" as $r |
  "\u001b[90m │ \u001b[0m" as $sep |
  (.model.display_name // "Gemini") as $model |
  (.cycle_mode // "auto") as $mode |
  (if (.vim.mode // "") != "" then " [\(.vim.mode)]" else "" end) as $vim |
  (if (.conversation_title // "") != "" then .conversation_title else ("#" + (.session_id // "new")[0:8]) end) as $raw_title |
  (if ($raw_title | length) > 40 then ($raw_title[0:37] + "...") else $raw_title end) as $title |
  ((.context_window.used_percentage // 0) | round) as $pct |
  (if $pct >= 80 then "\u001b[91m" elif $pct >= 50 then "\u001b[93m" else "\u001b[92m" end) as $ctx_color |
  "\u001b[96m\($model)\($r)\($sep)\u001b[95m\($mode)\($vim)\($r)\($sep)\u001b[97m\($title)\($r)\($sep)Context: \($ctx_color)\($pct)% used\($r)"
'
