import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Mimarchy's bar widget: one icon, one panel.
//
// Everything with a decision in it lives in `mimarchy-ctl`, not here. Plugins
// run unsandboxed inside the user's long-lived shell process, so a crash in
// this file is a crash in their desktop — and QML cannot be unit tested from
// this repo at all. What is left here is deliberately declarative: read a JSON
// document, draw it, and hand every interaction back to a subprocess.
//
// State arrives by two routes, for two different reasons:
//
//   * The lighting state file is *watched*. Effect, speed and link changes made
//     in the TUI show up here within a frame, with no polling and no process.
//   * Everything else — sensors, and whether the two units are running — needs
//     `mimarchy-ctl status --json`, so it is polled: quickly while the panel is
//     open, slowly while it is closed, since a shut panel only needs the icon
//     to notice the daemon stopping.
//
// The TUI stays the full control surface. This is the glanceable 90% plus the
// two adjustments worth having without opening a window.
Panel {
  id: root
  moduleName: "io.github.villenull.mimarchy"
  ipcTarget: "mimarchy"

  // ---- state -------------------------------------------------------------

  // The parsed `mimarchy-ctl status --json` document, or null before the first
  // successful read. Null is meaningfully different from "everything is off":
  // it is what "the backend is not installed" looks like, and the panel says so
  // rather than drawing a confident row of zeroes.
  property var status: null
  property bool backendMissing: false
  property real wheelAccumulator: 0

  readonly property bool lightingActive: status ? status.lighting_active === true : false
  readonly property bool displayActive: status ? status.display_active === true : false
  readonly property bool linked: status ? status.linked === true : true
  readonly property int speedStops: status && status.speed_stops ? status.speed_stops : 5

  readonly property var targetKeys: {
    if (!status || !status.targets) return []
    return Object.keys(status.targets)
  }

  // Linked means the pair is driven as one, so naming both on one row is the
  // honest summary; unlinked, each target speaks for itself.
  readonly property string effectSummary: {
    if (!status || targetKeys.length === 0) return "—"
    var first = status.targets[targetKeys[0]]
    if (!first) return "—"
    if (linked || targetKeys.length === 1) return first.effect
    var effects = targetKeys.map(function (k) { return status.targets[k].effect })
    var same = effects.every(function (e) { return e === effects[0] })
    return same ? effects[0] : effects.join(" / ")
  }

  readonly property bool anyTargetTakesSpeed: {
    for (var i = 0; i < targetKeys.length; i++)
      if (status.targets[targetKeys[i]].takes_speed) return true
    return false
  }

  // ---- what the panel can set --------------------------------------------

  // `effects.EFFECTS`, in its order — which is load-bearing twice over here.
  // It is the order the cells are drawn in, and it is what the 1-6/0 shortcuts
  // number, exactly as the TUI numbered them.
  readonly property var effects: ["static", "rainbow", "spectrum", "chase",
                                  "breathing", "unhinged", "off"]

  // `tui.PALETTE`, as hex literals rather than as an import, because the file
  // that holds them is deleted with the TUI and these are the values a user's
  // muscle memory is attached to. `arg` is what `mimarchy-ctl colour` is handed:
  // a role name for the theme chip, a hex string for the fixed swatches.
  //
  // The theme chip leads, and is a swatch rather than a mode, because
  // `colour_role` already *is* a colour choice underneath — "follow the
  // desktop" belongs next to the colours it competes with, not in a switch
  // somewhere else. `accent` is a member of `theme.LED_ROLES`; the other seven
  // roles stay reachable from `mimarchy-ctl colour <role>`.
  readonly property var swatches: [
    { arg: "accent",  colour: "" },
    { arg: "#ffffff", colour: "#ffffff" },
    { arg: "#ff0000", colour: "#ff0000" },
    { arg: "#ff5a00", colour: "#ff5a00" },
    { arg: "#00ff3c", colour: "#00ff3c" },
    { arg: "#00c8ff", colour: "#00c8ff" },
    { arg: "#1e64ff", colour: "#1e64ff" },
    { arg: "#ff00a0", colour: "#ff00a0" }
  ]

  // One entry per control block the panel draws.
  //
  // `key` is what `--zone` gets, and an empty one means the command is issued
  // *without* `--zone` — the every-target default that has always been there.
  // Linked deliberately takes that path rather than looping the zones itself:
  // it leaves every zone's own stored state agreeing with the one block on
  // screen, so unlinking later reveals what was being shown rather than three
  // zones that quietly drifted.
  //
  // `source` is the state entry a block *reads*. While linked that is the first
  // configured zone, because that is what `lightd._source_target` renders every
  // other zone from — reading anything else would draw a value the LEDs are not
  // showing.
  readonly property var blocks: {
    if (backendMissing || !status || targetKeys.length === 0) return []
    if (linked) return [{
      key: "",
      source: targetKeys[0],
      title: "All zones",
      subtitle: targetKeys.join(" · ")
    }]
    return targetKeys.map(function (k) {
      return { key: k, source: k, title: k.replace(/_/g, " "), subtitle: "" }
    })
  }

  // Every navigable row, flattened into one ordered list — the shape both
  // `bluetooth` and `tailscale` use, and for the same reason: a cursor over
  // nested sections needs one linear order to walk, and deriving it from the
  // same data that draws the rows is what keeps the two from disagreeing when
  // a row hides itself.
  //
  // `count` is how many cells the row holds horizontally, so `dx` has a bound
  // without asking the delegates.
  readonly property var rows: {
    var out = []
    if (backendMissing) return out
    out.push({ block: -1, field: "link", count: 1 })
    for (var i = 0; i < blocks.length; i++) {
      var t = (status && status.targets) ? status.targets[blocks[i].source] : null
      out.push({ block: i, field: "effect", count: effects.length })
      if (t && t.takes_colour) out.push({ block: i, field: "swatch", count: swatches.length })
      if (t && t.takes_speed) out.push({ block: i, field: "speed", count: speedStops })
    }
    out.push({ block: -1, field: "display", count: 1 })
    return out
  }

  function rowFor(blockIndex, field) {
    for (var i = 0; i < rows.length; i++)
      if (rows[i].block === blockIndex && rows[i].field === field) return i
    return -1
  }

  function targetFor(block) {
    if (!block || !status || !status.targets) return null
    return status.targets[block.source] || null
  }

  function targetColour(target) {
    if (!target || !target.colour) return Color.accent
    var c = target.colour
    return Qt.rgba(c[0] / 255, c[1] / 255, c[2] / 255, 1)
  }

  // Which swatch the zone is currently on, or -1 for a colour set from
  // somewhere else — `mimarchy-ctl colour '#123456'` is a legal thing to have
  // run, and marking nothing is more honest than marking the nearest.
  function selectedSwatch(target) {
    if (!target) return -1
    if (target.follows_theme) return 0
    var c = target.colour
    if (!c) return -1
    var hex = "#" + [c[0], c[1], c[2]].map(function (v) {
      var s = Math.max(0, Math.min(255, Math.round(v))).toString(16)
      return s.length < 2 ? "0" + s : s
    }).join("")
    for (var i = 1; i < swatches.length; i++)
      if (swatches[i].colour === hex) return i
    return -1
  }

  // ---- settings ----------------------------------------------------------

  readonly property bool showSensorsInTooltip: setting("showSensorsInTooltip", true)
  readonly property int pollIntervalSec: Math.max(1, setting("pollIntervalSec", 2))
  readonly property int idlePollIntervalSec: Math.max(5, setting("idlePollIntervalSec", 30))

  // ---- formatting --------------------------------------------------------

  function formatTemp(value) {
    return (value === null || value === undefined) ? "—" : Math.round(value) + "°"
  }

  function formatRpm(value) {
    return (value === null || value === undefined) ? "—" : Math.round(value) + " rpm"
  }

  function speedText(target) {
    if (!target || !target.takes_speed) return "no speed"
    return "speed " + target.speed_stop + "/" + speedStops
  }

  readonly property string tooltip: {
    if (backendMissing) return "Mimarchy — backend not installed"
    if (!status) return "Mimarchy"

    var parts = ["Mimarchy — " + effectSummary]
    if (!lightingActive) parts.push("lighting stopped")
    if (showSensorsInTooltip && status.sensors) {
      var s = status.sensors
      var readings = []
      if (s.cpu_temp !== null && s.cpu_temp !== undefined) readings.push("cpu " + formatTemp(s.cpu_temp))
      if (s.gpu_temp !== null && s.gpu_temp !== undefined) readings.push("gpu " + formatTemp(s.gpu_temp))
      if (readings.length > 0) parts.push(readings.join("  "))
    }
    return parts.join("\n")
  }

  // ---- reading state -----------------------------------------------------

  // Matches lightstate.STATE_PATH: XDG_RUNTIME_DIR, falling back to the config
  // home exactly as the Python side does, so the widget watches the same file
  // the daemon reads rather than a second guess at where it lives.
  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") || ""
  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME")
    || (Quickshell.env("HOME") + "/.config")
  readonly property string statePath: (runtimeDir !== "" ? runtimeDir : configHome)
    + "/mimarchy-lighting.json"

  // Watched rather than polled. A keypress in the TUI writes this file
  // atomically, so the bar follows the TUI without either knowing about the
  // other. The contents are not parsed here — the file changing is only used as
  // a signal to re-run status, which is the one place the shape is understood.
  FileView {
    path: root.statePath
    watchChanges: true
    printErrors: false
    onFileChanged: root.refresh()
    onLoaded: root.refresh()
  }

  Process {
    id: statusProcess
    running: false
    command: ["mimarchy-ctl", "status", "--json"]

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var parsed = JSON.parse(String(text || ""))
          root.status = (parsed && typeof parsed === "object") ? parsed : null
          root.backendMissing = false
        } catch (e) {
          // Malformed output is not the same as a missing backend, and is not
          // worth clearing a good previous reading over: keep what is on screen
          // and try again on the next tick.
          console.warn("mimarchy", "could not parse status", e)
        }
      }
    }

    // Whether *this* attempt got as far as a running process. Reset per launch,
    // because it is the only thing that separates the two ways a poll ends.
    property bool launched: false

    onStarted: statusProcess.launched = true

    onExited: function (exitCode) {
      // A backend that ran and failed. Distinct from the case below: this one
      // is installed, so the message it earns is about the exit code, not about
      // installing it.
      if (exitCode !== 0 && !root.status) root.backendMissing = true
    }

    // The case `onExited` cannot see. A command that is not on PATH never
    // starts, so Quickshell emits neither `started` nor `exited` — it logs
    // "Process failed to start" and drops `running` back to false with a null
    // processId, and that lone signal is the entire report. Watching for exit
    // 127 instead, which is what a *shell* would have returned, meant
    // `backendMissing` was never set at all: the panel fell through to
    // "Lighting daemon stopped — LEDs frozen", which sends someone to
    // `systemctl` over a program that was never installed.
    //
    // Measured rather than assumed, because the two orderings are what make
    // this safe: a healthy poll reports running=true, started, exited, then
    // running=false, so `launched` is always set before this runs.
    onRunningChanged: {
      if (running) return
      if (!launched && !root.status) root.backendMissing = true
      launched = false
    }
  }

  function refresh() {
    if (!statusProcess.running) statusProcess.running = true
  }

  // Actions are serialised rather than run concurrently, because two
  // `mimarchy-ctl` writes racing each other is exactly the interleaving the
  // atomic write is there to prevent — and a second one would read the state
  // before the first had written it.
  property var pendingAction: null

  Process {
    id: actionProcess
    running: false

    onExited: {
      // Re-read immediately rather than waiting for the next tick, so a click
      // feels like it did something. The state-file watch would catch lighting
      // changes anyway; this is what makes the display toggle prompt as well.
      root.refresh()

      // Dropping a click that arrived mid-flight is worse than delaying it: on
      // a toggle it shows as the switch flipping and snapping back. Only the
      // most recent is kept — a burst of scroll steps wants to end where the
      // user stopped, not replay every notch.
      if (root.pendingAction) {
        var next = root.pendingAction
        root.pendingAction = null
        root.run(next)
      }
    }
  }

  function run(args) {
    if (actionProcess.running) {
      root.pendingAction = args
      return
    }
    actionProcess.command = ["mimarchy-ctl"].concat(args)
    actionProcess.running = true
  }

  // Closed, this only has to notice the lighting daemon stopping, which is not
  // a per-second question. Open, it is drawing live temperatures.
  Timer {
    interval: (root.opened ? root.pollIntervalSec : root.idlePollIntervalSec) * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  onOpenedChanged: if (opened) refresh()

  // ---- the cursor --------------------------------------------------------

  // Visuals come from `CursorSurface`'s `hasCursor`/`current` and never from
  // `containsMouse`, which is the kit's contract: hovering moves this cursor
  // instead of drawing a second highlight, so there is exactly one on screen
  // whichever hand the user is using.
  property bool cursorActive: false
  property int cursorRow: 0
  property int cursorIndex: 0

  function rowCount(i) {
    var r = rows[i]
    return r ? Math.max(1, r.count) : 1
  }

  function clampCursor() {
    if (rows.length === 0) { cursorRow = 0; cursorIndex = 0; return }
    cursorRow = Math.max(0, Math.min(rows.length - 1, cursorRow))
    cursorIndex = Math.max(0, Math.min(rowCount(cursorRow) - 1, cursorIndex))
  }

  // A poll can delete the row the cursor is standing on: changing an effect to
  // `rainbow` removes that zone's whole swatch row a frame later.
  onRowsChanged: clampCursor()

  function cellHasCursor(row, index) {
    return cursorActive && row >= 0 && cursorRow === row && cursorIndex === index
  }

  function rowHasCursor(row) {
    return cursorActive && row >= 0 && cursorRow === row
  }

  function setCursor(row, index) {
    if (row < 0) return
    cursorActive = true
    cursorRow = row
    cursorIndex = index
    clampCursor()
  }

  // Clamped at both ends rather than wrapped, in both axes, because that is
  // what `bluetooth` and `tailscale` do — their `moveCursor` stops at the first
  // and last row rather than rolling over, and a panel that behaved differently
  // from the two beside it on the bar would be the odd one out.
  //
  // `dy` keeps the horizontal position rather than resetting it to 0: three
  // zones is three effect rows, and walking down the panel to compare the same
  // cell across them is the motion this layout exists for.
  function moveCursor(dx, dy) {
    if (rows.length === 0) return
    // The first press only reveals the cursor. Otherwise it appears one row
    // away from where the eye expects it — same as `bluetooth.moveCursorH`.
    if (!cursorActive) { cursorActive = true; clampCursor(); return }
    if (dy !== 0) cursorRow = Math.max(0, Math.min(rows.length - 1, cursorRow + dy))
    if (dx !== 0) cursorIndex = cursorIndex + dx
    clampCursor()
  }

  // ---- acting ------------------------------------------------------------

  function zoneArgs(block) {
    return (block && block.key !== "") ? ["--zone", block.key] : []
  }

  function chooseEffect(block, index) {
    if (!block || index < 0 || index >= effects.length) return
    run(["effect", effects[index]].concat(zoneArgs(block)))
  }

  function chooseColour(block, index) {
    if (!block || index < 0 || index >= swatches.length) return
    run(["colour", swatches[index].arg].concat(zoneArgs(block)))
  }

  // Absolute rather than stepped. The +/- shortcuts still walk the ladder, but
  // clicking the fourth segment of a five-segment card and watching it step one
  // notch is the kind of thing that makes a control feel broken.
  function chooseSpeed(block, index) {
    if (!block || index < 0 || index >= speedStops) return
    run(["speed", "set", String(index + 1)].concat(zoneArgs(block)))
  }

  // The block a number key acts on. On a control row that belongs to no zone —
  // either toggle — it falls back to the first block, which when linked is the
  // only one there is.
  readonly property var cursorBlock: {
    var r = rows[cursorRow]
    if (r && r.block >= 0) return blocks[r.block]
    return blocks.length > 0 ? blocks[0] : null
  }

  function activateCursor() {
    var row = rows[cursorRow]
    if (!row) return
    if (row.field === "link") { run(["link", "toggle"]); return }
    if (row.field === "display") { run(["display", "toggle"]); return }
    var block = blocks[row.block]
    if (row.field === "effect") chooseEffect(block, cursorIndex)
    else if (row.field === "swatch") chooseColour(block, cursorIndex)
    else if (row.field === "speed") chooseSpeed(block, cursorIndex)
  }

  // ---- bar icon ----------------------------------------------------------

  // U+F0335 (nf-md-lightbulb), the same glyph the Waybar module used. Renders
  // through the bar's own family: Omarchy sets that to `monospace`, which
  // fontconfig aliases to the user's Nerd Font, so this follows `omarchy font
  // set` rather than pinning a family the user did not choose.
  readonly property string glyph: "󰌵"

  // Dimmed when the LEDs are frozen — the daemon is stopped, the effect is
  // `off`, or the backend was never installed. That is the one piece of state
  // worth reading from across the room, so it is the one the icon spends its
  // only channel on.
  //
  // Expressed as `dimmed` rather than a darkened colour, because those are not
  // the same thing on a light theme: `barForeground` is near-black there, so
  // darkening it *raises* the contrast against the bar and the frozen icon
  // comes out louder than the live one. The base class dims by opacity, which
  // reads as "quieter" under both.
  readonly property bool lightsLive: lightingActive && effectSummary !== "off"

  // The bar sizes a widget from its implicit size, and `Panel` has none of its
  // own — so without this the root measures 0x0, the button anchored to it
  // fills nothing, and the icon is laid out with zero width. It still loads and
  // still reports itself as in the bar; it is simply never drawn, which is why
  // resolving every symbol in this file said nothing about it. Taken from the
  // button because the button is the whole of what this widget puts on the bar.
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // Set as `text` rather than drawn through `iconComponent`: the base class
    // then renders it through the bar's own font family and `Style.bar.iconFont`,
    // so it follows `omarchy font set` and matches the size of every other icon
    // on the bar — and it inherits the optical centring already written for
    // exactly this case. An `iconComponent` is for icons that are not glyphs.
    text: root.glyph
    tooltipText: root.tooltip
    dimmed: root.backendMissing || !root.lightsLive

    onPressed: function (buttonCode) {
      if (buttonCode === Qt.RightButton) root.run(["display", "toggle"])
      else if (buttonCode === Qt.MiddleButton) root.run(["link", "toggle"])
      else root.toggle()
    }

    // Accumulated rather than acted on per event, because a touchpad sends many
    // sub-notch deltas per gesture and one step per event would run the ladder
    // end to end on a single swipe.
    onWheelMoved: function (delta) {
      if (!root.anyTargetTakesSpeed) return
      var wheel = Util.wheelSteps(root.wheelAccumulator, delta)
      root.wheelAccumulator = wheel.remainder
      if (wheel.steps === 0) return
      root.run(["speed", wheel.steps > 0 ? "+" : "-"])
    }
  }

  // ---- panel -------------------------------------------------------------

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    // Wide enough for seven effect cells to carry their own names, which is
    // what sets the width — nothing else in here wants 420px.
    //
    // Uncapped in height, unlike before: unlinked, the panel is one full block
    // per zone and a fixed cap would silently clip the last one on a three-zone
    // rig. `fittedContentHeight` still bounds it by the screen.
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onMoveRequested: function (dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onTextKey: function (t) {
        var key = String(t || "").toLowerCase()
        // The same letters the TUI uses, so muscle memory carries over.
        //
        // These four stay global — every zone, cursor or no cursor. They are
        // the coarse controls the bar icon itself already speaks through its
        // wheel and its middle click, and scoping them to a cursor would make
        // the same keystroke mean two different things depending on a
        // highlight the user may not have summoned yet.
        if (key === "d") root.run(["display", "toggle"])
        else if (key === "u") root.run(["link", "toggle"])
        else if (key === "+" || key === "=") root.run(["speed", "+"])
        else if (key === "-") root.run(["speed", "-"])
        // The effect numbers, which are scoped. Pressing 3 is exactly the
        // cursor's own block moving to its third cell and committing, in one
        // keystroke rather than four — which is only meaningful because there
        // is now more than one block to be in.
        else if (key >= "1" && key <= "6")
          root.chooseEffect(root.cursorBlock, parseInt(key) - 1)
        else if (key === "0")
          root.chooseEffect(root.cursorBlock, root.effects.length - 1)
      }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Style.space(8)

        PanelSectionHeader {
          width: parent.width
          text: "Lighting"
        }

        // The backend-missing case gets a sentence rather than an empty panel:
        // a plugin installed before its Python side is a normal state to be in
        // for a minute, and the fix is one command.
        Text {
          width: parent.width
          visible: root.backendMissing
          text: "mimarchy-ctl not found.\nRun install.sh from the Mimarchy repo."
          wrapMode: Text.WordWrap
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        // Above everything it applies to, because it decides how much of the
        // rest there is: one block, or one per zone. A toggle underneath the
        // thing it reshapes reads as an afterthought to it.
        Toggle {
          width: parent.width
          visible: !root.backendMissing
          label: "Link all zones"
          description: root.linked ? "driven together" : "driven independently"
          checked: root.linked
          hasCursor: root.rowHasCursor(root.rowFor(-1, "link"))
          onClicked: root.run(["link", "toggle"])
          onHovered: function (isHovered) {
            if (isHovered) root.setCursor(root.rowFor(-1, "link"), 0)
          }
        }

        // Stated where it can be read before anything below it is touched: a
        // frozen strip answers every control in the panel identically, and
        // finding that out one click at a time is the bad version.
        Text {
          width: parent.width
          visible: !root.backendMissing && !root.lightingActive
          text: "Lighting daemon stopped — LEDs frozen."
          wrapMode: Text.WordWrap
          color: Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        Repeater {
          model: root.backendMissing ? [] : root.blocks

          ZoneBlock { width: column.width }
        }

        PanelSeparator { width: parent.width; visible: !root.backendMissing }

        // A toggle rather than a button, because it is a boolean state the
        // panel is already reporting — a control that shows its own value beats
        // a button whose label has to spell the value out.
        Toggle {
          width: parent.width
          visible: !root.backendMissing
          label: "Cooler display"
          // Worth saying plainly: the panel has no off command in its protocol,
          // so "off" means the telemetry stream stopped and the display blanks
          // itself about fifty seconds later.
          description: root.displayActive ? "streaming telemetry" : "blanks ~50s after stopping"
          checked: root.displayActive
          hasCursor: root.rowHasCursor(root.rowFor(-1, "display"))
          onClicked: root.run(["display", "toggle"])
          onHovered: function (isHovered) {
            if (isHovered) root.setCursor(root.rowFor(-1, "display"), 0)
          }
        }
      }
    }
  }

  // ---- previews ----------------------------------------------------------

  // Three LEDs running the effect, in the zone's own colour.
  //
  // Seven names are hard to tell apart in words — "breathing" and "spectrum"
  // both mean "one colour, changing", and "chase" and "rainbow" are both a
  // thing travelling along a strip. A preview is the shortest description
  // available and it shows the result you would actually get.
  //
  // Every effect derives from one looping clock rather than owning a hand-built
  // animation, because the alternative is seven animation graphs per cell and
  // twenty-one cells on screen while unlinked. `running` is gated on the panel
  // being open for the same reason the poll Timer is: a shut popover animating
  // sixty-three rectangles is a battery bug with nothing to show for it.
  component LedStrip: Item {
    id: strip

    required property string effect
    property color baseColour: "white"
    property bool animate: false

    // Two of the seven are still pictures, and a NumberAnimation that never
    // changes anything visible is still a NumberAnimation.
    readonly property bool animated: effect !== "static" && effect !== "off"

    property real clock: 0

    // Per effect, because they are not the same gesture at the same rate: a
    // travelling head wants to read as motion and a breath wants to read as
    // slow. Roughly the daemon's own middle-of-the-ladder timings.
    readonly property int period: {
      if (effect === "chase") return 1500
      if (effect === "unhinged") return 900
      if (effect === "breathing") return 3000
      return 3400
    }

    NumberAnimation on clock {
      running: strip.animate && strip.animated
      loops: Animation.Infinite
      from: 0
      to: 1
      duration: strip.period
    }

    function segmentColour(i) {
      var c = strip.clock
      switch (strip.effect) {
      // Continuous, and offset along the strip: a hue wave in space as well as
      // in time, which is what makes it a different picture from spectrum.
      case "rainbow":
        return Qt.hsva((c + i / 3) % 1, 0.85, 1, 1)
      // Deliberately quantised to five stops. Spectrum is a hue cycle the whole
      // zone shares, so a smooth sweep here would draw the same picture as
      // rainbow and the two cells would be indistinguishable.
      case "spectrum":
        return Qt.hsva(Math.floor(c * 5) / 5, 0.85, 1, 1)
      // Steppy on purpose, and out of step between segments. Unhinged is the
      // one effect whose whole character is that it does not ease.
      case "unhinged":
        return Qt.hsva((Math.floor(c * 6) * 0.37 + i * 0.21) % 1, 1, 1, 1)
      case "off":
        return Util.alpha(Color.popups.text, 0.18)
      default:
        return strip.baseColour
      }
    }

    function segmentOpacity(i) {
      var c = strip.clock
      // A head passing along the strip: full where it is, trailing off behind.
      if (strip.effect === "chase") {
        var d = ((c * 3) - i + 3) % 3
        return d < 1 ? 0.2 + 0.8 * (1 - d) : 0.2
      }
      // Synchronised, unlike chase — the whole zone rises and falls together.
      if (strip.effect === "breathing")
        return 0.2 + 0.8 * (0.5 - 0.5 * Math.cos(c * 2 * Math.PI))
      return 1
    }

    Row {
      anchors.fill: parent
      spacing: Style.space(2)

      Repeater {
        model: 3

        Rectangle {
          required property int index
          width: (strip.width - Style.space(2) * 2) / 3
          height: strip.height
          radius: Math.min(1, strip.height / 2)
          color: strip.segmentColour(index)
          opacity: strip.segmentOpacity(index)
        }
      }
    }
  }

  // ---- rows --------------------------------------------------------------

  component EffectCell: CursorSurface {
    id: cell

    required property int index
    required property string modelData
    property var block: null
    property int rowIndex: -1
    property string currentEffect: ""
    property color baseColour: "white"

    readonly property bool selected: modelData === currentEffect

    hasCursor: root.cellHasCursor(rowIndex, index)
    current: selected
    bordered: true

    Column {
      anchors.centerIn: parent
      width: parent.width - Style.space(4)
      spacing: Style.space(4)

      LedStrip {
        width: parent.width
        height: Style.space(7)
        effect: cell.modelData
        baseColour: cell.baseColour
        animate: root.opened
      }

      // The effect's real name, not an abbreviation of it: this is the word
      // `mimarchy-ctl effect <name>` takes, and a cell labelled "breathe" for
      // an effect called `breathing` teaches the wrong one.
      //
      // Fitted rather than elided, because a seventh of the panel is not a
      // width anyone chose — it is what seven cells leaves — and the user's own
      // font size decides whether "breathing" clears it. `HorizontalFit` shrinks
      // only the three names that need it and leaves the other four alone,
      // where eliding would cut every long name on every font.
      Text {
        width: parent.width
        text: cell.modelData
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideNone
        fontSizeMode: Text.HorizontalFit
        minimumPixelSize: Math.max(6, Style.font.caption - 3)
        color: cell.selected ? Color.popups.text : Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setCursor(cell.rowIndex, cell.index)
      onClicked: root.chooseEffect(cell.block, cell.index)
    }
  }

  component SwatchCell: CursorSurface {
    id: swatch

    required property int index
    required property var modelData
    property var block: null
    property int rowIndex: -1
    property bool selected: false

    readonly property bool isTheme: modelData.colour === ""

    width: Style.space(26)
    height: Style.space(26)
    hasCursor: root.cellHasCursor(rowIndex, index)
    current: selected

    Rectangle {
      anchors.centerIn: parent
      visible: !swatch.isTheme
      width: Style.space(15)
      height: width
      radius: width / 2
      color: swatch.isTheme ? "transparent" : swatch.modelData.colour
      border.width: 1
      border.color: Qt.rgba(0, 0, 0, 0.35)
    }

    // The theme chip. A sweep rather than a colour, because "follow the
    // desktop" is not one hue — it is whichever one the active theme names.
    // QtQuick has no conical gradient outside Qt5Compat, so this is a vertical
    // multi-stop instead: the same "all of them" reading, one import lighter.
    Rectangle {
      anchors.centerIn: parent
      visible: swatch.isTheme
      width: Style.space(15)
      height: width
      radius: width / 2
      border.width: 1
      border.color: Qt.rgba(0, 0, 0, 0.35)

      gradient: Gradient {
        GradientStop { position: 0.0;  color: Qt.hsva(0.00, 0.75, 1, 1) }
        GradientStop { position: 0.25; color: Qt.hsva(0.12, 0.75, 1, 1) }
        GradientStop { position: 0.5;  color: Qt.hsva(0.33, 0.70, 1, 1) }
        GradientStop { position: 0.75; color: Qt.hsva(0.58, 0.75, 1, 1) }
        GradientStop { position: 1.0;  color: Qt.hsva(0.80, 0.70, 1, 1) }
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setCursor(swatch.rowIndex, swatch.index)
      onClicked: root.chooseColour(swatch.block, swatch.index)
    }
  }

  // One stop of the speed ladder. A card of discrete segments rather than a
  // `PanelSlider`, because the backend has exactly five stops and a continuous
  // track would be a picture of something that does not exist.
  component SpeedStop: CursorSurface {
    id: stop

    required property int index
    property var block: null
    property int rowIndex: -1
    property int currentStop: 0

    readonly property bool filled: index < currentStop

    hasCursor: root.cellHasCursor(rowIndex, index)

    Rectangle {
      anchors.verticalCenter: parent.verticalCenter
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.leftMargin: Style.space(2)
      anchors.rightMargin: Style.space(2)
      height: Style.space(4)
      radius: height / 2
      color: stop.filled ? Color.accent : Util.alpha(Color.popups.text, 0.18)

      Behavior on color { ColorAnimation { duration: 90 } }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setCursor(stop.rowIndex, stop.index)
      onClicked: root.chooseSpeed(stop.block, stop.index)
    }
  }

  // ---- one zone ----------------------------------------------------------

  // Everything one zone can be set to, in one block: effect, colour, speed.
  // Linked draws exactly one of these for every zone at once; unlinked draws
  // one each. There is no third layout and no selected-row state to keep in
  // sync, which is the whole reason the link toggle sits above it.
  component ZoneBlock: Column {
    id: zone

    required property var modelData
    required property int index

    readonly property var target: root.targetFor(modelData)
    readonly property color baseColour: root.targetColour(target)
    readonly property int effectRow: root.rowFor(index, "effect")
    readonly property int swatchRow: root.rowFor(index, "swatch")
    readonly property int speedRow: root.rowFor(index, "speed")

    spacing: Style.space(6)
    topPadding: Style.space(2)

    PanelSeparator { width: zone.width }

    Item {
      width: zone.width
      height: zoneName.implicitHeight

      Text {
        id: zoneName
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: zone.modelData.title
        color: Color.popups.text
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Text {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: zone.target ? zone.target.effect : "—"
        color: Color.accent
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }
    }

    // Only the linked block has one: it names the zones this single set of
    // controls is actually driving, which is the question "All zones" raises
    // and does not answer.
    Text {
      width: zone.width
      visible: zone.modelData.subtitle !== ""
      text: zone.modelData.subtitle
      elide: Text.ElideRight
      color: Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
    }

    Row {
      id: effectCells
      width: zone.width
      spacing: Style.space(3)

      Repeater {
        model: root.effects

        EffectCell {
          width: (effectCells.width - effectCells.spacing * (root.effects.length - 1))
            / root.effects.length
          height: Style.space(34)
          block: zone.modelData
          rowIndex: zone.effectRow
          currentEffect: zone.target ? zone.target.effect : ""
          baseColour: zone.baseColour
        }
      }
    }

    // Hidden when the effect ignores colour, on the backend's own word:
    // `takes_colour` is `effect in effects.COLOUR_EFFECTS` computed in
    // `ctl.cmd_status`, so the panel does not carry a second copy of that set
    // to fall out of date with the first.
    Row {
      width: zone.width
      spacing: Style.space(4)
      visible: zone.target ? zone.target.takes_colour === true : false

      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: "colour"
        color: Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }

      Repeater {
        model: root.swatches

        SwatchCell {
          block: zone.modelData
          rowIndex: zone.swatchRow
          selected: root.selectedSwatch(zone.target) === index
        }
      }
    }

    Column {
      width: zone.width
      spacing: Style.space(4)
      visible: zone.target ? zone.target.takes_speed === true : false

      Item {
        width: parent.width
        height: speedLabel.implicitHeight

        Text {
          id: speedLabel
          anchors.left: parent.left
          text: "speed"
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        Text {
          anchors.right: parent.right
          text: (zone.target ? zone.target.speed_stop : "—") + " / " + root.speedStops
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      Row {
        id: speedStopsRow
        width: parent.width
        spacing: Style.space(4)

        Repeater {
          model: root.speedStops

          SpeedStop {
            width: (speedStopsRow.width - speedStopsRow.spacing * (root.speedStops - 1))
              / root.speedStops
            height: Style.space(14)
            block: zone.modelData
            rowIndex: zone.speedRow
            currentStop: zone.target ? zone.target.speed_stop : 0
          }
        }
      }
    }
  }
}
