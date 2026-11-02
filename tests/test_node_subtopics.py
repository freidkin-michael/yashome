"""NODE_SUBTOPICS: a plug-in reads <node>/<name>/... of any node, never the core's home/ or a claimed namespace."""
import unittest
from types import SimpleNamespace
from unittest import mock

import app


def msg(topic, payload=b"x", retain=True):
    return SimpleNamespace(topic=topic, payload=payload, retain=retain)


class NodeSubtopics(unittest.TestCase):
    def test_a_node_topic_reaches_the_plugin_and_nothing_else_does(self):
        seen = []
        with mock.patch.dict(app.NODE_SUBTOPICS, {"fwgen": lambda parts, p, r: seen.append((parts, p, r))}), \
                mock.patch.object(app, "MQTT_NAMESPACES", {"zigbee2mqtt"}):
            app._mqtt_on_message(None, None, msg("radar-1/fwgen/hash", b"3f9a1c07be52"))
            app._mqtt_on_message(None, None, msg("home/fwgen/hash"))               # the core's own topic
            app._mqtt_on_message(None, None, msg("zigbee2mqtt/fwgen/hash"))        # a claimed namespace
            app._mqtt_on_message(None, None, msg("radar-1/other/hash"))            # not a subtopic of a plug-in
            app._mqtt_on_message(None, None, msg("radar-1/fwgen"))                 # no third level
        assert seen == [(["radar-1", "fwgen", "hash"], b"3f9a1c07be52", True)]

    def test_the_subscription_is_made_for_each_name(self):
        cli = mock.Mock()
        with mock.patch.dict(app.NODE_SUBTOPICS, {"fwgen": lambda *a: None}), \
                mock.patch.object(app, "_mqtt_topic_index", lambda: {}), \
                mock.patch.object(app, "_mqtt_publish_state", lambda d: None):
            app._mqtt_subscribe_all(cli)
        assert mock.call("+/fwgen/#") in cli.subscribe.call_args_list


if __name__ == "__main__":
    unittest.main()
