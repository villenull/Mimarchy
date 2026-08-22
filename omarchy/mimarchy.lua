-- Float the Mimarchy TUI, for Omarchy 4.
--
-- Append this line to ~/.config/hypr/hyprland.lua. The `o` helper is already in
-- scope there — Omarchy's bootstrap defines it before the user config runs, and
-- the stock file ends with a commented `o.window(...)` example for exactly this.
--
-- Why this is needed at all: Omarchy 4 floats a fixed list of app-ids plus the
-- `TUI.float` class, and an app-id it has not heard of tiles. `omarchy-launch-tui`
-- names windows `org.omarchy.<command>`, so the TUI arrives as
-- `org.omarchy.mimarchy-tui` and needs to be added to the floating set by name.
--
-- The `terminal` tag (opacity and theming for terminal windows) is picked up
-- automatically: Omarchy matches `org\.omarchy\..*` for that one, so only the
-- floating half has to be asked for.
--
-- The tag is what to attach to, not `float = true` directly: `floating-window`
-- also carries centring and the standard 875x600 size, so tagging keeps the TUI
-- the same shape as every other Omarchy float instead of inventing its own.

o.window("org.omarchy.mimarchy-tui", { tag = "+floating-window" })
