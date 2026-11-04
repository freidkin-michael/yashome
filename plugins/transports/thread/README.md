# thread - the house's Thread network

Thread for Yashome: the house's own network over one or more border routers (OpenThread OTBR), its nodes, pairing.
The nodes themselves (yashGen ESPHome nodes) are ordinary MQTT devices of the house: they reach the house's broker over
the border router's NAT64; this plug-in does the network around them. Requires `computers`.

## Border routers: a role of a computer

A border router is a computer of the house (plug-in computers) with the role **Thread border router**, installed from the
computer's card (Settings -> Devices -> Computers and servers): a radio is picked from the computer's `/dev/serial/by-id`
(the probe lists them; likely Thread radios first) and a baud rate (115200, 460800, 1000000; guessed from the name). The
house's own computer is a computer like any other: the role goes there over ssh too. Any number of routers; one network.

`helper/roles/thread-install.sh` (`sudo sh -s` there, its arguments checked by the helper):

- docker when missing; refused while another OTBR container runs there (it would fight for the radio and `wpan0`);
- the kernel modules `iptable_nat` and `iptable_filter` loaded and kept in `/etc/modules-load.d/yashome-thread.conf`
  (the OTBR's NAT44 uses iptables-legacy);
- `/opt/yashome-thread/compose.yml`: `openthread/otbr` pinned by digest, container `yashome-otbr`, host network,
  privileged, `tmpfs: /run` (a restart hangs on a stale pid without it), `/dev:/dev`, `./data:/var/lib/thread`,
  `FIREWALL=0` (its ip6tables-legacy firewall fails on nftables hosts), REST and web page on `127.0.0.1` (8081, 8089),
  `--radio-url spinel+hdlc+uart:///dev/serial/by-id/<radio>?uart-baudrate=<baud>` (no `uart-flow-control`: the
  parameter turns flow control on), `--trel-url trel://<if> --backbone-interface <if>` on the interface of the default route
  (without `--trel-url` TREL does nothing); a second service `routes` (container `yashome-otbr-routes`, same image)
  runs `routes.sh loop`;
- `/opt/yashome-thread/routes.sh`: each border router announces the shared Thread prefixes on the LAN in its RAs; the
  other one takes that route (the kernel, as the OTBR sets `accept_ra_rt_info_max_plen=64` at every start; NetworkManager
  always, metric 105, again within seconds when deleted) over its own `wpan0` route (256), and the two routers loop the
  packets: NAT64 answers never reach a node. routes.sh sets the sysctl back to 0 and pins every `wpan0` prefix that also
  has an RA route on the backbone to `wpan0` at metric 10 (the pin goes with `wpan0`). Every 10 s, and in `ctl.sh status`;
  it prints and logs only what it changed (`ROUTES_CONF`: another sysctl folder than `/proc/sys/net/ipv6/conf`, for the tests);
- `/opt/yashome-thread/ctl.sh` (root only): `status` (routes fixed, running, state, leader weight, TREL peers, the SRP
  hosts this router serves: `srp=<host> <address>`), `restart`, `leaderweight <n>`, `joiner` (`<eui64|*> <PSKd>` on its
  stdin). The helper runs it (`POST /v1/hosts/thread`) and nothing else there.

`helper/roles/thread-remove.sh` takes the container, the folder and the module list away.

**The REST has no auth: it never leaves the router's loopback.** The house reaches router N through the helper's ssh tunnel
`thread<N>` (`helper:8180+N` -> that computer's `127.0.0.1:8081`, slots 1-8, kept in the settings).

## The network is the house's

- The active dataset is kept in `plugin-state/thread/dataset.hex` (0600). It is never logged, never in an answer or a
  health row; the page shows only the name, channel and extended PAN id.
- The **main** router (Settings -> Devices -> Thread -> Network; default: the first computer with the role) takes the
  network: the house keeps the dataset the main router already has; with none there, the main router forms one (the name
  and channel of the settings, default `Yashome`, 15; the rest random) and the house keeps it.
- Every other router with no dataset gets the house's (`PUT /node/dataset/active`, then `enable`). A router on another
  network (another extended PAN id or key) is a red health row and is never overwritten: remove the role and install it again.
- **Leader weight**: the main router 255, the others 64, checked every 5 minutes and at once when the router's state
  (REST) is not the one `ctl.sh status` saw last (`ot-ctl` does not keep it over a restart).
  This replaces a cron script on the router's host.

## Health (Settings -> Overview)

One row per router: its REST answers, its radio (RCP) answers (`/node/coprocessor/version`), it is in the house's network,
and with more than one router it sees another over the LAN (TREL peers > 0). One row "Thread leader": the main router leads.
A router whose agent does not answer while its container runs, whose radio is gone, or that left the network, is restarted
(`docker compose restart` through ctl.sh) after 3 checks in a row (a check a minute), at most once in 10 minutes; the restart
and its cause stay on its row for 30 minutes.

## Nodes

Every 5 minutes the main router is asked for its device collection (`updateDeviceCollectionTask`, a task list of its
own: with a network diagnostic of the router itself in the same list it fails), a check later for the diagnostics (child
table, router neighbours) of every other router (one task runs at a time, so each next one gets a longer timeout). The list
(Settings -> Devices -> Thread -> Nodes) shows each node's role, its parent (a child), its links (a router: each neighbour's
RSSI and link quality 0-3; the main router's from its neighbours' tables) and its best link. Names: the device collection's
host name, else the SRP host of its OMR address on ANY router (`ctl.sh status`: a node registers with one SRP server,
often not the main router's), else the computer name of a border router. A node is the house device of that name (an
ESPHome node's id, `esphome_node` or topic prefix such as `home/thread-node-a`, `-` or `_`), or the one picked by hand
(kept by EUI-64 or extended address; `POST /api/thread/node`, no control in the UI). A node with no house device gets "Add to the house": its ESPHome
device by the host name (`POST /api/esphome/add`, the name asked), then it is matched again; a node with one shows that
device's name and opens its card, where a Thread section gives its role and links. A house device that is a Thread
router gets a "T" mark on the home map (`window.MapMarks`, `GET /api/thread/marks`).

## Pairing

Add -> Thread (or Settings -> Devices -> Thread -> Pairing; owner only), one button: hold the device's button 5 s (it
blinks blue), press Let it in. The commissioner on the main router lets in for 3 minutes any device with the public joiner
code of the Yash firmware (`PUBLIC_PSKD`, yashGen builds it in; like Zigbee's well-known key it is no secret: only an open
pairing lets a device in). TREL makes the routers one network: a device near another router joins too.

Then the house pairs it itself (`_auto_pair`, until 2 minutes after the window): every SRP host of the routers and every
node's OMR address (a node that forgot its network has a new SRP key; its old name stays held by the old one) first
gets an empty request; a Yash node waiting for the house answers 400 with its name, a silent one is asked again in
8 s; a name that is not online and not a device the house has (its own kept card does not count) gets the pair request, through `ctl.sh pair <IPv6>` on the main
router (the form on stdin) to the node's `/yash/pair` (plug-in pair: `pair_with`, `thread_form`; no code, no Wi-Fi: the
request comes from inside the Thread network). The node keeps its own broker account and OTA password, reboots onto the
broker, and the pair plug-in makes its card from its description. A node that never answers (an older or a foreign
firmware) gets no account. The pairing's status lists the nodes paired.

Another Thread device (`POST /api/thread/pair` with `pskd`, no control in the UI: such devices are rare, Matter ones
use their own code): its PSKd from the label and its EUI-64 when known. With an EUI-64: the REST task
`addThreadDeviceTask`, followed to joined / timeout / failed; without: `ctl.sh joiner` with `*`, and "joined" when a new
node shows in the device collection. A PSKd is not kept, logged or answered; it reaches ctl.sh on its stdin. Matter devices
join the network this way but get no card (no Matter controller yet).

Routes (house token): `GET|PATCH /api/thread` (`main`, `name`, `channel`), `GET /api/thread/marks`, `POST /api/thread/node`
(`node`, `device`), `POST|GET /api/thread/pair`.

| file | what |
|---|---|
| `__init__.py` | the routers' checks, the dataset, leader weight, restarts, nodes, pairing, health rows, routes, `role_active` |
| `ui.js`, `i18n.json` | Settings -> Devices -> Thread (Network, Nodes, Pairing), Add -> Thread, the map mark |
| `requires` | `computers` |
| `setup/settings.example.json` | the settings section `thread` |
| `../../../helper/roles/thread-install.sh`, `thread-remove.sh` | the role on a computer |

## Limits

- The OTBR web page (127.0.0.1:8089) is not proxied; reach it with an ssh tunnel by hand.
- One radio per computer (one OTBR container); a radio on a network (Ethernet) bridge is not a role option yet.
- A node list entry needs the device collection of OTBR REST 0.3 or newer (the pinned image has it).
