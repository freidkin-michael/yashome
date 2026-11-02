#pragma once
// Helpers of provision.yaml: an on-demand Wi-Fi scan that keeps the connection, the relay command
// parser and the portal form encoding. ESPHome has no scan action, and reading the esp_wifi records
// directly returns nothing (ESPHome's own SCAN_DONE handler drains them first), so the scan asks
// ESPHome to keep full results and reads its parsed list ~3.5 s later, without blocking the loop.

#include <esp_wifi.h>
#include "esphome/components/wifi/wifi_component.h"
#include "esphome/core/log.h"
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace home_provision {

inline std::string json_escape(const std::string &in) {
  std::string out;
  for (char c : in) {
    if (c == '"' || c == '\\')
      out += '\\';
    if ((unsigned char) c >= 0x20)
      out += c;
  }
  return out;
}

/// "AA:BB:CC:DD:EE:FF"
inline std::string mac_str(const esphome::wifi::bssid_t &b) {
  char buf[18];
  snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X", b[0], b[1], b[2], b[3], b[4], b[5]);
  return buf;
}

inline void scan_start() {
  // without this ESPHome keeps only the networks it is configured for
  esphome::wifi::global_wifi_component->set_keep_scan_results(true);
  wifi_scan_config_t cfg{};
  cfg.show_hidden = false;
  cfg.scan_type = WIFI_SCAN_TYPE_ACTIVE;
  esp_wifi_scan_stop();
  esp_err_t err = esp_wifi_scan_start(&cfg, false);
  if (err != ESP_OK)
    ESP_LOGW("provision", "scan_start: %s", esp_err_to_name(err));
}

/// JSON report: every OPEN network (an unprovisioned device) and every "*<suffix>" one, also a secured
/// one; the RSSI is the one THIS node measures, which is what picks the closest relay. At most 12.
inline std::string scan_report(const char *node, const char *suffix) {
  const auto &found = esphome::wifi::global_wifi_component->get_scan_result();
  ESP_LOGI("provision", "scan: %u networks in total", (unsigned) found.size());
  const size_t sl = strlen(suffix);
  std::string out = std::string("{\"node\":\"") + node + "\",\"aps\":[";
  unsigned kept = 0;
  for (const auto &r : found) {
    std::string ssid(r.get_ssid().c_str(), r.get_ssid().size());
    const bool is_setup = ssid.size() > sl && ssid.compare(ssid.size() - sl, sl, suffix) == 0;
    const bool is_open = !r.get_with_auth();
    if (ssid.empty() || !(is_setup || is_open))
      continue;
    if (++kept > 12)
      break;
    char buf[128];
    snprintf(buf, sizeof(buf), "\",\"bssid\":\"%s\",\"rssi\":%d,\"ch\":%u,\"open\":%s,\"setup\":%s}",
             mac_str(r.get_bssid()).c_str(), (int) r.get_rssi(), (unsigned) r.get_channel(),
             is_open ? "true" : "false", is_setup ? "true" : "false");
    out += std::string(kept > 1 ? "," : "") + "{\"ssid\":\"" + json_escape(ssid) + buf;
  }
  return out + "]}";
}

/// The SSID this node is associated with now ("" when not connected). The relay judges by this, not by
/// wifi.configure's on_connect/on_error, which cannot tell "could not join" from "already back home".
inline std::string current_ssid() {
  char buf[esphome::wifi::SSID_BUFFER_SIZE];
  const char *cur = esphome::wifi::global_wifi_component->wifi_ssid_to(buf);
  if (!esphome::wifi::global_wifi_component->is_connected() || cur == nullptr)
    return "";
  return std::string(cur);
}

inline bool on_ssid(const std::string &want) { return !want.empty() && current_ssid() == want; }

/// The relay joins by name (wifi.configure has no BSSID); this pins it: no credentials go to an AP whose
/// MAC is not the one the backend saw. An empty `want` (an old scan without MACs) passes.
inline bool on_bssid(const std::string &want) {
  return want.empty() || mac_str(esphome::wifi::global_wifi_component->wifi_bssid()) == want;
}

inline std::string current_bssid() { return mac_str(esphome::wifi::global_wifi_component->wifi_bssid()); }

/// The credentials this node runs on NOW (saved by a portal, or the compiled ones): after a Wi-Fi
/// password change one node fixed by hand can recover the rest. Call BEFORE wifi.configure; false when
/// it does not know them (nothing is handed over then).
inline esphome::wifi::WiFiAP &captured_sta() {
  static esphome::wifi::WiFiAP sta;
  return sta;
}

inline bool capture_creds(std::string &ssid, std::string &psk) {
  auto sta = esphome::wifi::global_wifi_component->get_sta();
  captured_sta() = sta;                     // the whole network (manual IP, hidden, BSSID) to go back to
  ssid.assign(sta.get_ssid().c_str(), sta.get_ssid().size());
  psk.assign(sta.get_password().c_str(), sta.get_password().size());
  ESP_LOGI("provision", "will hand over network '%s' (%s)", ssid.c_str(), psk.empty() ? "no password" : "password set");
  return !ssid.empty();
}

/// Back to the network it ran on, as captured (its manual IP, hidden flag, BSSID included), without
/// wifi.configure (its timeout saves the previous network, here the setup AP) and without saving
/// anything: what is in flash stays as it was; a reboot brings the node's own networks back.
inline void go_home() {
  auto *w = esphome::wifi::global_wifi_component;
  w->disable();
  w->set_sta(captured_sta());
  w->enable();
}

/// The setup-AP key: the command's, else for a "*<suffix>" AP this node's own compiled ap_password (a
/// house node is built with the shared key, so the backend sends it empty). A "*<suffix>" AP is never
/// joined open; any other name with no key is joined open. Returns the refusal, "" when fine.
inline std::string pick_key(const std::string &ap, const std::string &own, const char *suffix, std::string &key) {
  const size_t sl = strlen(suffix);
  const bool setup = ap.size() > sl && ap.compare(ap.size() - sl, sl, suffix) == 0;
  if (key.empty() && setup)
    key = own;
  if (key.empty() && setup)
    return "refused: an open -setup AP: not one of the house's nodes (no ap_password compiled in)";
  return "";
}

inline std::string urlenc(const std::string &in) {
  static const char *HEX = "0123456789ABCDEF";
  std::string out;
  for (unsigned char c : in) {
    if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
      out += (char) c;
    } else {
      out += '%';
      out += HEX[c >> 4];
      out += HEX[c & 0x0F];
    }
  }
  return out;
}

inline void judge(int status, bool &ok, std::string &detail) {
  ok = status < 400;
  detail = ok ? std::string("the portal took the credentials; the node restarts")
              : "the portal answered HTTP " + std::to_string(status);
}

/// "<ap>|<host>|<METHOD>|<path>|<ssid_field>|<psk_field>|<bssid>|<id>|<ap_password>"; empty fields fall
/// back to the ESPHome portal (192.168.4.1, POST /wifisave, ssid/psk); the AP password is the rest of the
/// line. The same rules as the backend: host 192.168.4.x, path and field charsets, no control characters;
/// `error` says what was refused (nothing is run then).
struct PortalSpec {
  std::string ap, ap_psk, host{"192.168.4.1"}, path{"/wifisave"}, ssid_field{"ssid"}, psk_field{"psk"};
  std::string bssid, id, error;
  bool get{false};
};

inline bool all_of(const std::string &v, const char *extra, size_t max) {
  if (v.empty() || v.size() > max)
    return false;
  for (unsigned char c : v)
    if (!isalnum(c) && strchr(extra, c) == nullptr)
      return false;
  return true;
}

inline bool portal_host_ok(const std::string &h) {
  const std::string pre = "192.168.4.";
  if (h.compare(0, pre.size(), pre) != 0)
    return false;
  std::string rest = h.substr(pre.size()), port;
  size_t colon = rest.find(':');
  if (colon != std::string::npos) {
    port = rest.substr(colon + 1);
    rest = rest.substr(0, colon);
    if (!all_of(port, "", 5) || port.find_first_not_of("0123456789") != std::string::npos ||
        atoi(port.c_str()) < 1 || atoi(port.c_str()) > 65535)
      return false;
  }
  if (!all_of(rest, "", 3) || rest.find_first_not_of("0123456789") != std::string::npos)
    return false;
  int n = atoi(rest.c_str());
  return n >= 1 && n <= 254;
}

inline bool bssid_ok(const std::string &b) {
  if (b.size() != 17)
    return false;
  for (size_t i = 0; i < 17; i++)
    if (i % 3 == 2 ? b[i] != ':' : !isxdigit((unsigned char) b[i]))
      return false;
  return true;
}

inline PortalSpec parse_do(const std::string &payload) {
  PortalSpec s;
  std::string *dst[] = {&s.ap, &s.host, nullptr, &s.path, &s.ssid_field, &s.psk_field, &s.bssid, &s.id};
  size_t i = 0;
  for (int f = 0; f < 9 && i <= payload.size(); f++) {
    size_t j = f == 8 ? payload.size() : payload.find('|', i);
    if (j == std::string::npos)
      j = payload.size();
    std::string v = payload.substr(i, j - i);
    if (f == 8)
      s.ap_psk = v;
    else if (f == 2 && !v.empty())
      s.get = v == "GET" || v == "get";
    else if (dst[f] != nullptr && (!v.empty() || f >= 6))
      *dst[f] = v;
    i = j + 1;
  }
  for (unsigned char c : payload)
    if (c < 0x20 || c == 0x7F)
      s.error = "refused: a control character in the command";
  for (auto &c : s.bssid)
    c = (char) toupper((unsigned char) c);
  if (s.error.empty() && !portal_host_ok(s.host))
    s.error = "refused: the portal host must be 192.168.4.x";
  else if (s.error.empty() && (s.path[0] != '/' || !all_of(s.path, "/_.?=&-", 96)))
    s.error = "refused: the portal path";
  else if (s.error.empty() && !(all_of(s.ssid_field, "_", 32) && all_of(s.psk_field, "_", 32)))
    s.error = "refused: the portal field names";
  else if (s.error.empty() && !s.bssid.empty() && !bssid_ok(s.bssid))
    s.error = "refused: the bssid";
  else if (s.error.empty() && !s.id.empty() && !all_of(s.id, "", 16))
    s.error = "refused: the attempt id";
  else if (s.error.empty() && (s.ap.empty() || s.ap.size() > 32))
    s.error = "refused: the AP name";
  return s;
}

/// provision/<node>/result: {"node","ap","id","ok","busy","detail"}
inline std::string result_json(const char *node, const std::string &ap, const std::string &id, bool ok, bool busy,
                               const std::string &detail) {
  return std::string("{\"node\":\"") + node + "\",\"ap\":\"" + json_escape(ap) + "\",\"id\":\"" + json_escape(id) +
         "\",\"ok\":" + (ok ? "true" : "false") + ",\"busy\":" + (busy ? "true" : "false") + ",\"detail\":\"" +
         json_escape(detail) + "\"}";
}

}  // namespace home_provision
