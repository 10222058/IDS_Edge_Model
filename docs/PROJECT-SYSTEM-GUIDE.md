# IoT-SecureGate Intelligent Protection Module

## Project guide for presentations and interviews

This guide explains the system in plain language: what problem it solves, how its
parts work together, what to demonstrate, and which limitations to describe
honestly.

## 1. The project in one sentence

The project packages an IoT gateway monitoring and protection system into a
desktop application: an operator can set up monitoring, provision a gateway,
inspect gateway activity, and configure a rule-based plus machine-learning IDS
without manually running scripts or Docker on the normal deployment path.

## 2. The problem and the solution

IoT gateways receive messages from connected sensors. A compromised, misconfigured,
or malfunctioning device can send unusual traffic, such as a burst of messages,
unexpected payloads, or suspicious timing patterns. In the original workflow,
operators had to coordinate separate scripts and services to install the gateway
software, view logs, and manage protection.

This project brings those tasks into one operator workflow:

1. Start the desktop app and prepare the local monitoring services.
2. Discover or enter a gateway and connect to it securely over SSH.
3. Install the gateway agent and register the gateway in the app.
4. Review monitoring data and IDS alerts.
5. Adjust protection settings and export an alert report.

The app integrates an existing IDS into the packaged gateway workflow. The focus is
on integration and usability, not claiming that the underlying machine-learning
algorithm was invented by this project.

## 3. System at a glance

```text
 Sensors / MQTT clients
          |
          | MQTT messages
          v
+------------------------------ Gateway device ------------------------------+
|  Mosquitto broker                                                          |
|       |                                                                     |
|       +--> Decrypt worker --> structured sensor log --+                    |
|       |                                                |                    |
|       +--> Mosquitto connection log ------------------+--> Rule + ML IDS   |
|                                                            |               |
|                         health / metrics <--- agent         +--> alert log |
+---------------------------------------------------------------|-------------+
                                                                |
                          HTTP log push (Loki) / metrics scrape |
                                                                v
+------------------------------- Operator PC -------------------------------+
|  Desktop UI <--> local control API <--> SSH to gateway                    |
|       |                                                                    |
|       +--> Loki (logs and alerts)    Prometheus (metrics)                  |
|       |                                                                    |
|       +--> alert review and HTML / CSV / JSON report export                 |
+----------------------------------------------------------------------------+
```

The normal installation path does not require Docker on the operator PC or the
gateway. Loki and Prometheus run as native processes on the operator PC. On the
gateway, Mosquitto and the agent are managed as native services. A legacy Compose
deployment remains available for users who intentionally use that older mode.

## 4. Main components

| Component | Where it runs | What it does |
|---|---|---|
| Desktop app | Operator's Windows or Linux PC | Serves the setup wizard and app pages, starts the local control service, and manages the operator workflow. |
| Local control service | Operator PC | Provides API endpoints for gateway actions, monitoring queries, and IDS configuration/alerts. |
| Loki | Operator PC | Stores logs and IDS alert events pushed by the gateway agent. |
| Prometheus | Operator PC | Collects metrics exposed by the gateway agent. |
| Gateway agent | Gateway device, as a systemd service | Supervises Mosquitto and the helper workers; exposes health/metrics; advertises the gateway with mDNS; starts the IDS. |
| Decrypt worker | Gateway device, within the agent deployment | Subscribes to sensor MQTT topics, decrypts supported wrapped payloads, and appends structured events to a JSON-lines log. |
| IDS | Gateway device, as a child process of the agent | Reads sensor and broker logs, applies enabled behavioral rules and ML scoring, and writes JSON-lines alerts. |
| Provisioner | Control service on the operator PC | Connects to the gateway over SSH and installs or updates the agent and IDS files. |

The packaged UI is local to the operator PC. Monitoring data is queried through
the control service from local Loki/Prometheus; gateway configuration changes are
sent over SSH.

## 5. How a sensor message becomes an alert

1. A sensor publishes an MQTT message to the gateway's Mosquitto broker.
2. The gateway's decrypt worker subscribes to sensor topics, decrypts the supported
   payload wrapper, and writes the decoded event and timestamps to
   `sensor_data.log` in JSON-lines format.
3. The IDS reads the sensor log and the local Mosquitto connection log. In the
   legacy Compose configuration, it can also be configured to read HAProxy logs.
4. The IDS groups accepted sensor events into **3-second windows per device** and
   extracts numeric features. It also reads broker and, where configured, other
   gateway logs for rule context.
5. The behavioral rule engine evaluates the configured rules first. A rule
   violation generates a rule alert immediately for that event/window. If no rule
   rejects it, the ML detector may score the window when enabled and when it meets
   the minimum message count.
6. The ML detector's scores are smoothed over multiple windows. The IDS writes a
   machine-learning alert only after the configured confirmation condition is met;
   a single high-scoring window is not necessarily enough.
7. Alerts are written as structured JSON lines to the gateway's `ids-alerts.log`.
8. The agent ships new sensor and IDS log lines to Loki. IDS alerts use the
   `ids-alerts` job label.
9. The desktop Security Center requests alerts through the control-service API.
   The operator can filter them, mark them reviewed in the current browser profile,
   and export the displayed alert data.

### What is passed into the ML model?

The model does **not** receive a raw network packet or an entire MQTT payload as a
single input. For each eligible device/window, the IDS converts the accepted
sensor-event records into a row of 12 numeric features. The shipped model metadata
identifies these feature columns:

| Feature | What it represents in the window |
|---|---|
| `msg_count` | Number of sensor messages. |
| `topic_unique_count` | Number of distinct non-empty MQTT topics. |
| `topic_flood_ratio` | Fraction of topics whose text contains `flood`. |
| `avg_payload_len` | Mean payload length in bytes (or serialized characters for non-string structures). |
| `std_payload_len` | Standard deviation of payload lengths. |
| `min_payload_len` | Smallest payload length. |
| `max_payload_len` | Largest payload length. |
| `mean_inter_arrival_ms` | Mean time between consecutive messages, in milliseconds. |
| `std_inter_arrival_ms` | Standard deviation of those inter-arrival times. |
| `avg_delay` | Mean available message delay value, clamped to non-negative values. |
| `max_delay` | Maximum available message delay value, clamped to non-negative values. |
| `attack_blob_ratio` | Fraction of payloads containing a truthy `debug.attack_blob` (or `attackblob`) field. |

In plain terms, the ML model sees **summaries of message volume, topic use, payload
size, timing, delay, and a payload indicator**. It does not directly receive the
device IP, a textual attack description, or the Mosquitto connection statistics
as columns in the current 12-feature model. Those log sources can inform the
behavioral rules, but the deployed feature list is the one above.

The input feature names/order come from the packaged model metadata when available,
and the runtime fills absent feature values with zero. The metadata says the
feature scaler is disabled for the shipped model. The model is loaded from the
packaged `ids_model.joblib`; it is **inference-only** during normal operation and
is not retrained or updated from live traffic.

### Which algorithm does the model use?

The configuration and model metadata identify the classifier as **LightGBM**, a
gradient-boosted decision-tree model. In broad terms, it combines many decision
trees in sequence: later trees help correct errors made by earlier trees. The
runtime asks the classifier for class probabilities and uses the probability for
class `1` as the attack score. In the model metadata, class `0` is normal and class
`1` is attack.

The deployed model's metadata records its training dataset as
`ids_features_gateway_message_v2.csv`, its target column as `label`, and its split
strategy as grouped by `session_id`. The training pipeline/source code and training
dataset are not included in the repository, so the exact training procedure cannot
be independently reproduced from this codebase alone. Describe the method as the
LightGBM model packaged with the project; do not imply that the live gateway trains
itself.

The shipped metadata reports a held-out test set of 516 windows (462 normal and 54
attack) with 100% precision and recall, 0% false-positive rate, and ROC-AUC 1.0 at
the selected threshold. These are **the model metadata's recorded results**, not a
new experiment performed by this guide. Because the dataset and training pipeline
are not included here and the test set is limited, present them as preliminary
reported results—not as proof of performance on all gateways or unseen attack
types. A repeatable evaluation on separately collected data is still important.

### How the rule-based detector works

The rule engine is a set of **explicit "if this happens, raise this alert" checks**.
Unlike the LightGBM model, it does not calculate a learned probability. It compares
observed events and log counts to conditions and thresholds in the IDS YAML
configuration. This makes a rule easy to explain: for example, "the device sent
messages faster than the configured limit."

```text
Accepted sensor events ──┐
                         ├──> Group by device / time ──> Check enabled rules
Denied-event records ────┤                                    │
                         │                          Any rule matches?
Mosquitto connection ────┤                              /          \
and publish logs ────────┘                            YES           NO
                                                       │             │
                                      Write named rule alert     ML may score
                                      (no device blocking)       this window

Optional gateway-state / HAProxy logs supply extra rule signals when available.
```

In normal operation, accepted sensor messages are grouped into 3-second windows.
Some checks use that window (for example message timing); others use rolling
60-second counters or gateway state (for example denied-event ratio or ACL
membership). The rule engine returns the first matching rule for that evaluation.
It limits repeat alerts for the same device and rule using a configurable cooldown
(60 seconds in the shipped YAML). During the cooldown, that rule does not create a
duplicate alert.

#### Rule categories and examples

The thresholds below are the defaults in `gateway/ids/configs/ids_config.yaml`;
an operator can change supported values in Security → Protection Settings. Rules
that require a log or gateway-state source only work when that source is present
and contains the needed data.

| Category | Plain-language question | Example rules and shipped default conditions |
|---|---|---|
| **Message rate and timing** | Is this sensor sending too fast or reporting an implausible time? | `high_message_rate`: over 1.5 messages/second; `accelerated_publishing`: any interval below 900 ms; `rapid_fire`: at least 3 messages with mean interval below 80 ms; `clock_skew_anomaly`: reported delay over 2.5 seconds; `future_timestamp`: device timestamp is ahead of gateway receive time. |
| **Payload shape** | Is an accepted message's size outside the expected sensor profile, or are there repeated rejected/small payloads? | `accepted_size_drift`: accepted payload size is not 2769 or 2793 bytes; `payload_reject_storm`: at least 5 rejections in the rolling window; `undersize_payload_campaign`: at least 3 denied payloads smaller than 2500 bytes in 60 seconds; `denied_probe_burst`: at least 5 consecutive denied events without accepted traffic; `deny_accept_ratio`: denied events exceed 10% of denied plus accepted events in 60 seconds. |
| **Identity and gateway state** | Does the sensor identity agree with the gateway's registered ACL, active-device, and IP information? | `unknown_acl_user`: device identity is absent from a populated Mosquitto ACL user list; `inactive_sensor_traffic`: device is absent from a populated active-sensor list; `source_ip_not_allowed`: observed IP is outside a populated allow-list; `source_ip_baseline`: observed IP differs from the device's configured baseline; `cross_ip_identity`: accepted events for one device identity are seen from multiple IPs; `mqtt_multi_ip_connect`: broker logs show one MQTT identity connecting from multiple IPs. |
| **MQTT connection behavior** | Is a device repeatedly reconnecting or creating more connections/identities than its publishing behavior suggests? | `mqtt_connect_churn`: connection rate over 1.5/second or at least 10 connects in the configured log window; `connect_publish_ratio`: more than 1.15 connections per publish; `ephemeral_client_churn`: unique ephemeral client IDs exceed 1.25 times the publish count. |
| **TLS / coordinated signals** | Are there many TLS handshake failures, or do multiple independent warning signs occur together? | `ssl_handshake_storm`: at least 10 HAProxy handshake failures per source IP in 60 seconds; `attack_triad`: configured to combine a denied ratio over 10%, connect/publish ratio over 1.15, and at least 10 TLS failures. |

#### A simple example

Suppose one sensor normally sends about one message each second, with payloads
around its expected size:

1. The rule engine measures the messages in the current window.
2. If the measured rate exceeds 1.5 messages/second, `high_message_rate` matches.
3. The IDS writes a warning alert naming that rule and its observed rate.
4. That evaluation is treated as a rule detection; the ML detector is not run for
   that same window.
5. The gateway records and ships the alert. The current system does not
   automatically disconnect or block the sensor.

If no rule matches, the window may continue to ML scoring. This is how explicit
known-pattern checks complement the model's broader learned pattern scoring.

#### Which rules are available on the default gateway agent?

The rule engine supports more signals than every deployment necessarily provides.
On the default Docker-free agent, the expected inputs are decoded sensor events
and Mosquitto logs. This supports message rate/timing and payload checks, plus MQTT
connection checks when the broker log parser has usable records. Denied-event
checks need denied-event records; the default decrypt worker primarily writes
successfully decoded sensor events, so do not assume Node-RED rejection rules are
active in this path.

Identity and IP rules depend on gateway state (such as a populated ACL, allowed-IP,
or active-sensor list). The default agent currently records source IP as
`"unknown"`, so real-IP allow-list and per-device IP-baseline checks are not
reliable there. HAProxy TLS rules and the composite TLS-based `attack_triad` need
HAProxy failure logs, which are not connected in the default agent path.

One implementation detail for accurate presentations: individual rules are
evaluated in sequence and the first match can take precedence. In particular,
`ssl_handshake_storm` is checked before `attack_triad`; when the TLS-failure
threshold is reached, the standalone SSL rule can return first. Treat the triad
as a configured combined condition that should be verified in the specific
deployment before claiming that it produces its own alert.

### How do the ML model and behavioral rules cooperate?

They are **two complementary detection mechanisms in one IDS**, with rules checked
before ML for each sensor window:

1. **Rules look for explicit conditions.** Examples include unusually high
   message rates, clock skew, unexpected payload sizes, suspicious MQTT connection
   churn, identity/ACL inconsistencies, and (where those log sources exist) TLS
   handshake failures.
2. **A rule violation can alert on its own.** The detector returns a rule failure
   before performing ML inference for that window. The IDS creates a
   `behavioral_rule` alert with the rule name/reason. Some log-derived rule checks
   can also produce alerts when there are denied events but no accepted sensor
   message window.
3. **If rules do not flag the window, ML can score it.** When ML is enabled and
   enough records are present, the model returns an attack probability. This can
   identify a pattern represented in the training data even if no individual
   configured rule fired.
4. **ML scores need confirmation over time.** The IDS keeps a rolling history per
   device. With the shipped configuration, it examines up to 6 windows, waits for
   at least 4 windows, then confirms if either at least 3 windows are above the
   per-window threshold and the attack vote ratio is at least 60%, **or** the
   rolling average score reaches 0.68. This reduces alerts caused by one isolated
   score spike.
5. **The alert explains which path fired.** Rule alerts identify the behavioral
   rule. ML alerts use `message_pattern_anomaly` and include an ML probability and
   rolling-score details.

The configured per-window model threshold and the rolling average confirmation
threshold are separate: the packaged model metadata sets the per-window threshold
to **0.05** when the IDS configuration leaves it unspecified, while the shipped
IDS YAML sets the rolling average confirmation threshold to **0.68**. The default
agent configuration can be edited through the Security Center, so actual values
may differ on a configured gateway.

Rules and ML can be toggled separately. Disabling ML leaves rule checks available;
disabling rules leaves ML scoring available. Disabling the overall IDS prevents
the detector process from starting.

### What can the model and rules detect in this deployment?

- The feature-based model is intended to distinguish normal and attack-labelled
  message windows based on patterns in the 12 inputs above.
- Behavioral rules target known conditions, including rate/timing anomalies,
  payload patterns, and broker or gateway-state signals.
- In the default Docker-free agent, the IDS reads decrypted sensor events and
  Mosquitto logs. HAProxy/TLS signals are only available in a deployment that
  actually provides those logs, such as the legacy Compose configuration.
- The default decrypt worker currently records the sensor source IP as `"unknown"`.
  Consequently, rules requiring the real sensor IP cannot be relied on in this
  path.
- Detection depends on the data and patterns available to these mechanisms; neither
  the LightGBM model nor the rules guarantee detection of every attack.

## 6. What the operator can do

### Setup and gateway management

- Install or start local monitoring services through the setup wizard.
- Discover and register gateways.
- Pin SSH host identity and provision the gateway agent.
- Manage registered gateways and supported gateway actions.

### Monitoring

- View gateway metrics and activity through the desktop app.
- Use Loki for logs and Prometheus for metrics.
- The agent exposes health and metrics endpoints for monitoring and readiness.

### Security Center

- Select a registered gateway and view its recent IDS alerts.
- Filter alerts by time range, severity, text, and gateway.
- Mark alerts reviewed; review markers are stored in the browser's local storage,
  not as an acknowledgement on the gateway.
- Enable or disable the IDS, behavioral rules, and ML detector.
- Tune supported thresholds and behavioral parameters.
- Apply configuration over SSH; applying it requires the gateway sudo password and
  restarts the gateway agent.
- Export alert data as HTML, CSV, or JSON.

Reports are exported from the alerts returned to the UI for the selected query and
filters. They are useful operator reports, but are not currently a signed,
forensically immutable audit record.

## 7. Communication and trust boundaries

- **Operator PC to gateway:** SSH is used for provisioning, configuration reads,
  and configuration updates.
- **Sensor to gateway:** MQTT carries sensor traffic to Mosquitto.
- **Gateway to operator PC:** the agent pushes logs to Loki over HTTP, and
  Prometheus scrapes the agent's metrics endpoint. These connections need network
  reachability and appropriate firewall rules.
- **Desktop UI to local services:** the UI calls the local control API; the API
  queries local Loki/Prometheus or performs gateway operations.

The operator PC's Loki endpoint must be reachable from the gateway. On Linux, the
host firewall may need to permit inbound TCP port 3100. Windows setup includes a
guided firewall step. SSH credentials and the sudo password should be treated as
secrets; the sudo password is supplied when applying IDS settings and is not part
of the saved IDS configuration.

## 8. Current scope and limitations

Be clear about these points when presenting the project:

- The default agent IDS uses decrypted sensor events and Mosquitto logs.
- HAProxy/TLS-specific signals are not part of the default Docker-free agent path;
  they are available only where the legacy Compose deployment provides those
  logs.
- The default decrypt-worker event currently records source IP as `"unknown"`.
  Therefore, rules that depend on actual sensor source IP are limited on this
  path.
- The IDS raises and records alerts; it does **not** automatically block a device.
- Mark-reviewed status is kept in browser local storage rather than a shared
  server-side review database.
- The report export is a UI-generated export of queried alerts, not a signed
  compliance or forensic report.
- The repository contains automated tests for important API, provisioner, agent,
  and desktop behavior. A complete measured evaluation of detection accuracy,
  resource use, and end-to-end performance should be reported only if those
  experiments have actually been run and documented.
- Existing gateways installed before IDS integration need to be provisioned again
  to receive the IDS runtime and model.

These are useful future-work topics: add device-blocking controls with safe
operator confirmation, capture reliable source identity, persist review state
centrally, strengthen report provenance, and run a repeatable performance and
detection evaluation.

## 9. Suggested live demonstration

Use a test gateway and test sensor data. Do not generate attack traffic against
devices or networks you do not own or have permission to test.

1. **Introduce the architecture:** show the operator PC and gateway sides in the
   diagram above.
2. **Show setup:** launch the desktop app and show the monitoring/gateway workflow.
   If the environment is already installed, explain that first-run setup is a
   one-time step rather than uninstalling a working setup just for the demo.
3. **Show gateway status:** open Gateways or Monitoring and identify the registered
   gateway and its health/activity.
4. **Show protection controls:** open Security → Protection Settings and explain
   the IDS, rules, ML, and threshold controls. Avoid changing a production
   gateway's settings during a presentation.
5. **Show alerts:** open Security → Security Alerts and explain severity, device,
   detection reason, score, and review status.
6. **Show reporting:** export an HTML report for a selected period, then explain
   that CSV and JSON are also available.
7. **Finish with limitations and next steps:** especially automatic blocking,
   source-IP visibility, and measured evaluation.

If no live alert is available, use a pre-approved test dataset or a screenshot
captured from a controlled test. Do not claim a live attack detection unless the
demonstration actually exercised it.

## 10. Short presentation scripts

### 30-second version

> My project turns an IoT gateway IDS from separate research components into a
> usable operator workflow. A desktop application prepares monitoring, connects to
> a gateway over SSH, and provides gateway management and a Security Center. On
> the gateway, an agent supervises MQTT, decrypts supported sensor messages, and
> runs behavioral rules plus an ML detector. Alerts are sent to local Loki and can
> be reviewed and exported from the UI. The current version detects and reports;
> automatic device blocking and full performance evaluation remain future work.

### Two-minute version

> The problem I addressed was that gateway monitoring and intrusion detection
> required users to coordinate multiple services and scripts. I integrated the
> existing rule-based and machine-learning IDS into a desktop-managed workflow.
>
> The operator PC runs the cross-platform desktop application, a local control
> service, Loki, and Prometheus. From the UI, the operator registers a gateway and
> provisions it over SSH. The gateway runs a systemd-managed agent with Mosquitto,
> a decrypt worker, the IDS, and log/metrics reporting.
>
> When a sensor publishes a message, the decrypt worker writes a structured event.
> The IDS reads that event and broker connection logs, evaluates behavioral rules,
> and—when enabled—scores eligible windows with the ML model. It writes any
> detections as structured alerts, which the agent ships to Loki. The Security
> Center queries those alerts, lets an operator review them, tune protection
> settings, and export HTML, CSV, or JSON.
>
> The project demonstrates integration across desktop UX, gateway provisioning,
> telemetry, and IDS operations. It currently alerts rather than blocks devices.
> Source-IP visibility is limited on the default agent path, and measured detection
> and resource-use results need to be presented only after a controlled evaluation.

## 11. Questions you may be asked

**What is the main contribution?**  
Integrating gateway IDS operation into an installable desktop-managed workflow:
provisioning, configuration, alert viewing, and report export around the existing
rule and ML detection components.

**Why use both rules and machine learning?**  
Rules express known behavioral conditions and remain independently configurable.
The ML model scores message-window features for patterns represented by its
training. Combining them gives the operator two detection mechanisms; it does not
guarantee every attack will be detected.

**Where does the data go?**  
Sensor events and IDS alerts are logged on the gateway and shipped to the
operator's local Loki service. Prometheus collects gateway metrics. Gateway
management and configuration travel over SSH.

**Does the system block an attacker automatically?**  
No. The current integrated IDS records and presents alerts. Automatic blocking is
not implemented in this version.

**How do you know the model is effective?**  
Explain the evaluation dataset, metrics, thresholds, and test procedure only if
you have run and recorded them. Otherwise, state that a controlled detection and
resource evaluation remains future work.

**What would you improve next?**  
Add carefully confirmed response actions such as device blocking, capture
trustworthy source identity in the default path, store review decisions centrally,
and conduct documented accuracy, latency, and resource-consumption experiments.

## 12. Repository map

| Area | Location | Responsibility |
|---|---|---|
| Desktop shell and launch | `apps/desktop/shell/` | Starts the UI and local control service; handles desktop setup and lifecycle. |
| Desktop screens | `apps/desktop/ui/` | Wizard, monitoring, gateway management, Security Center, and settings pages. |
| Control API | `apps/control-service/api/` | Gateway operations, telemetry queries, IDS configuration, and alert API. |
| Provisioning | `apps/control-service/provisioner/` | Installs the default gateway agent and supporting IDS assets over SSH. |
| Gateway agent | `gateway/agent/iot_gateway_agent/` | Broker supervision, decrypt worker, IDS process lifecycle, telemetry shipping, health, and metrics. |
| IDS engine | `gateway/ids/ids_gateway/` | Log capture, feature building, behavioral rules, ML inference, and alert creation. |
| Native monitoring services | `apps/control-service/telemetry/` | Downloads, configures, and manages local Loki and Prometheus binaries. |
| Windows packaging guide | `apps/desktop/WINDOWS-BUILD.md` | Instructions for producing the Windows executable. |
| Main project overview | `README.md` | Product workflow, platform behavior, and architecture summary. |
