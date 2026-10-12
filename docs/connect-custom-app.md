# Connect your custom app to Automiq

Connect an app by registering its event types in the Automiq Studio. Your app then sends signed events to its connection URL. A published workflow can react to those events and, if you configure a callback, send a signed event back to your app. You do not need an Automiq code change for each app.

The quick start uses a task app that sends `com.example.task.submitted.v1` and receives `com.example.task.acknowledged.v1`. A [PR-evidence example](#task-app-example-pr-evidence) below shows the changes needed in a real task app. The acknowledgement means the workflow reached its callback step; it does **not** verify a PR merge or close a task.

## 1. Prepare your app

Choose an event type and the data your app will send. This example sends a task ID. Keep the data small and limited to fields the workflow needs. Do not send passwords, API keys, or other credentials in event data.

If your app should receive callbacks, expose a public HTTPS `POST` endpoint, for example `https://tasks.example.com/automiq/events`. In a normal deployment, Automiq requires a named host on port 443 with a valid TLS certificate. IP addresses, localhost, private destinations, URL queries, and redirects are not supported for callbacks. Your endpoint must read the **raw request body** so it can verify the signature before parsing JSON.

The Automiq event URL shown after connection creation comes from `API_PUBLIC_URL`. If your app runs on another machine, the Automiq operator must set that URL to an address your app can reach. A local `localhost` URL is only useful when the sending app runs on the same machine.

## 2. Create the connection in Studio

1. Sign in to `/studio` as a workspace **owner**, then create or select a workspace and a workflow.
2. Scroll to **Connections → Integrations & triggers → Custom apps**. Under **Add custom app**, enter a name.
3. Enter your app's HTTPS callback URL if you want Automiq to send events back. Leave it empty for an app that only sends events. Paste the following JSON into **Incoming event types and schemas**:

   ```json
   {
     "com.example.task.submitted.v1": {
       "type": "object",
       "properties": { "task_id": { "type": "string" } },
       "required": ["task_id"],
       "additionalProperties": false
     }
   }
   ```

4. If you configured a callback URL, paste this into **Outgoing event types and schemas**. Otherwise, enter `{}`:

   ```json
   {
     "com.example.task.acknowledged.v1": {
       "type": "object",
       "properties": { "task_id": { "type": "string" } },
       "required": ["task_id"],
       "additionalProperties": false
     }
   }
   ```

5. Select **Create connection**. Copy the connection's **webhook URL** and the two secrets into your app's secret manager:

   | Value | Used by your app for |
   | --- | --- |
   | Webhook URL | Sending events to Automiq |
   | Inbound signing secret | Signing events your app sends |
   | Callback verification secret | Checking callbacks Automiq sends |

   The secrets appear only when the connection is created or rotated. The webhook URL remains visible in the connection list. If you miss a secret, use **Rotate** and update your app with the new values; rotation immediately invalidates the old values.

Schemas currently support a flat JSON object with up to 30 named fields. Each field may be a `string`, `integer`, `number`, `boolean`, or `null`. Declare required fields and use `"additionalProperties": false`. Nested objects, arrays, and undeclared fields are not accepted. To add or change event types in the current version, create another connection.

## 3. Publish and bind a workflow

1. In the workflow editor, choose **App event** from the node palette. This replaces the starting trigger.
2. Connect the trigger to the work you want to run, and connect the final step to **End**. For this example, add an **App callback** action before **End**.
3. Select the callback node. Choose your custom app and `com.example.task.acknowledged.v1`. Set the `task_id` source path to `trigger.payload.task_id`. This maps the incoming task ID into the callback.
4. **Save draft**, validate it, then **Publish**.
5. In **Integrations & triggers**, choose the custom app and `com.example.task.submitted.v1`, then select **Bind app event**. Binding is to the published version. If you publish a later version, bind that version too and disable an old binding if it should stop receiving events.

Create the binding before sending a test event. An accepted event with no active binding returns `202` with an empty `run_ids` list, so no workflow starts.

## 4. Send a signed event

Your app sends one [CloudEvents 1.0 JSON](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md) object to the exact webhook URL returned by Automiq. For example:

```json
{
  "specversion": "1.0",
  "id": "task-submission-001",
  "source": "urn:example:task-app",
  "type": "com.example.task.submitted.v1",
  "datacontenttype": "application/json",
  "data": { "task_id": "task-123" }
}
```

Sign the **exact UTF-8 bytes** sent over HTTP. The HMAC-SHA-256 input is `timestamp + "." + body`, where `timestamp` is Unix time in seconds. Send these headers:

```text
Content-Type: application/cloudevents+json
X-Automiq-Timestamp: <Unix seconds>
X-Automiq-Signature: sha256=<lowercase hex HMAC-SHA-256>
```

This Python standard-library example reads the connection values from environment variables. In your app, keep the signing secret in a secret manager and persist a stable event ID with the task before sending:

```python
import hashlib
import hmac
import json
import os
import time
from urllib.request import Request, urlopen

event = {
    "specversion": "1.0",
    "id": os.environ["APP_EVENT_ID"],  # Reuse this ID and body on retry.
    "source": "urn:example:task-app",
    "type": "com.example.task.submitted.v1",
    "datacontenttype": "application/json",
    "data": {"task_id": os.environ["APP_TASK_ID"]},
}
body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
timestamp = str(int(time.time()))
digest = hmac.new(
    os.environ["AUTOMIQ_INBOUND_SECRET"].encode("utf-8"),
    timestamp.encode("ascii") + b"." + body,
    hashlib.sha256,
).hexdigest()
request = Request(
    os.environ["AUTOMIQ_WEBHOOK_URL"],
    data=body,
    method="POST",
    headers={
        "Content-Type": "application/cloudevents+json",
        "X-Automiq-Timestamp": timestamp,
        "X-Automiq-Signature": f"sha256={digest}",
    },
)
with urlopen(request, timeout=10) as response:
    print(response.status, json.load(response))
```

A successful response is `202` with `event_id`, `run_ids`, `status: "accepted"`, and `duplicate`. It means Automiq durably accepted the event, **not** that a workflow or callback has finished. If delivery fails, resend the same event ID and identical body with a fresh timestamp and signature. Automiq deduplicates by connection, `source`, and `id`; the same ID with a changed body returns `409`.

## 5. Verify and handle callbacks

When the workflow reaches an **App callback** node, Automiq posts a CloudEvent to the fixed callback URL. It uses the **callback verification secret**, not the inbound signing secret, and the same timestamp and signature headers. The event has a stable `id` for that workflow run and node, `source` in the form `urn:automiq:workspace:<workspace-id>`, `subject` in the form `runs/<run-id>`, the configured callback `type`, and schema-checked `data`.

Verify the raw bytes before trusting the event. This framework-neutral Python function can be called from your HTTPS handler; `headers` should support case-insensitive lookup:

```python
import hashlib
import hmac
import json
import os
import time


def verify_automiq_callback(raw_body: bytes, headers):
    timestamp = headers.get("X-Automiq-Timestamp", "")
    signature = headers.get("X-Automiq-Signature", "")
    if not timestamp.isdecimal() or len(timestamp) > 12:
        raise ValueError("Invalid timestamp")
    if abs(time.time() - int(timestamp)) > 300:
        raise ValueError("Expired callback")
    expected = hmac.new(
        os.environ["AUTOMIQ_OUTBOUND_SECRET"].encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, f"sha256={expected}"):
        raise ValueError("Invalid signature")
    event = json.loads(raw_body)
    if event["specversion"] != "1.0" or event["type"] != "com.example.task.acknowledged.v1":
        raise ValueError("Unexpected event")
    if not isinstance(event.get("data", {}).get("task_id"), str):
        raise ValueError("Invalid callback data")
    return event
```

After verification, store the callback `id` and apply your app's business update **in one database transaction**. If that ID was already processed, return a 2xx response without applying the update again. Return 2xx only after the transaction commits. Automiq can retry callbacks, so an app must make this step idempotent. The callback status is available to signed-in workspace members at `GET /api/v1/workspaces/{workspace-id}/app-connections/{connection-id}/callbacks`.

## Task app example: PR evidence

Suppose a developer submits `https://github.com/example/widgets/pull/42` as evidence for task `task-123`. The task app must own the task record, evidence record, and final status change. Automiq receives an event and runs a configured workflow; it does not directly edit the task app's database.

### Changes needed in the task app

| Area | Add or change |
| --- | --- |
| Evidence form and API | Accept a GitHub PR URL for developer evidence. Parse and validate the repository and PR number; save the original URL. A commit hash may be stored as extra evidence, but it does not reliably identify one PR by itself. Keep docs and file evidence on the existing creator-review path. |
| Persistence | Store `task_id`, `evidence_id`, PR URL, repository, PR number, submitter, and evidence state. Add an outbound-event outbox with a stable event ID and serialized body. Add a callback-receipt table with a unique callback event ID. |
| Event sender | After evidence and its outbox row commit together, a background job signs the saved body with the **inbound** secret and posts it to the connection's webhook URL. Retry transient failures with the same event ID and identical body, using a fresh timestamp/signature. Treat `202` as intake confirmation only. |
| Callback endpoint | Expose `POST /automiq/events` over public HTTPS. Verify the raw-body HMAC and timestamp with the **outbound** secret, check the event type and task/evidence IDs, and save the callback ID with the resulting update in one transaction. Return 2xx for an already-processed ID. |
| Configuration | Store the webhook URL and both secrets outside source code. On rotation, update both app secrets; on revocation, stop sending events. |

Create a separate connection for this PR-evidence example if you already made the quick-start connection; current connection schemas cannot be edited. Register `com.example.task.pr_evidence_submitted.v1` as an **incoming** event with this schema:

```json
{
  "com.example.task.pr_evidence_submitted.v1": {
    "type": "object",
    "properties": {
      "task_id": { "type": "string" },
      "evidence_id": { "type": "string" },
      "repository": { "type": "string" },
      "pr_number": { "type": "integer" }
    },
    "required": ["task_id", "evidence_id", "repository", "pr_number"],
    "additionalProperties": false
  }
}
```

The saved outbox body for this submission could be:

```json
{
  "specversion": "1.0",
  "id": "evidence-ev-789",
  "source": "urn:example:task-app",
  "type": "com.example.task.pr_evidence_submitted.v1",
  "datacontenttype": "application/json",
  "data": {
    "task_id": "task-123",
    "evidence_id": "ev-789",
    "repository": "example/widgets",
    "pr_number": 42
  }
}
```

Create this body **once** when evidence is submitted. Use the [signing procedure in step 4](#4-send-a-signed-event) to send its exact saved bytes. A practical app-side flow is:

```text
POST /tasks/task-123/evidence  { pr_url: "https://github.com/example/widgets/pull/42" }
  authorize the submitter and validate the PR URL
  begin database transaction
    insert evidence ev-789 with state = submitted
    insert outbox event evidence-ev-789 with the exact CloudEvent body above
  commit
  return the saved evidence to the developer

background sender
  load an unsent outbox row
  sign and POST its saved bytes to the Automiq webhook URL
  on 202, mark the outbox row accepted by Automiq; on a transient failure, retry it
```

**What works now:** You can bind this event to a workflow and send an acknowledgement callback with `task_id` and `evidence_id`. Register this as an **outgoing** event on the same new connection:

```json
{
  "com.example.task.evidence_received.v1": {
    "type": "object",
    "properties": {
      "task_id": { "type": "string" },
      "evidence_id": { "type": "string" }
    },
    "required": ["task_id", "evidence_id"],
    "additionalProperties": false
  }
}
```

In the **App callback** node, map `task_id` from `trigger.payload.task_id` and `evidence_id` from `trigger.payload.evidence_id`. Use the [callback verifier in step 5](#5-verify-and-handle-callbacks) with this event type instead of the quick-start acknowledgement type. After verifying a callback, the task app can mark the evidence as received by automation:

```text
POST /automiq/events
  verify timestamp and signature over the raw request body
  require type = com.example.task.evidence_received.v1
  begin database transaction
    if callback id already exists, commit and return 204
    load evidence by evidence_id and task_id; reject unknown or mismatched IDs
    set evidence automation state = received
    insert callback receipt with a unique callback id
  commit and return 204
```

Do not change the task to **closed** on this acknowledgement: it says nothing about the PR's merge state.

**When PR-merge tracking is added to Automiq:** Configure a workflow that checks the exact repository and PR, handles an already-merged PR, and waits for a verified later merge. A future `com.example.task.pr_merged.v1` callback would include the task ID, evidence ID, repository, and PR number. The task app should close the task only after verifying that signed callback **and** matching those fields to its saved PR evidence. Record the callback ID and close the task in one transaction so retries cannot repeat the change. If a PR closes without merging, keep the task open. Any linked PR that is verified merged may satisfy the task without creator review; docs, files, and other URL evidence still follow creator review.

That PR-merge workflow is **not available in the current Automiq build**. It requires the planned durable event correlation and GitHub PR-status integration. Because connection schemas are immutable at version 1, adding the future merged callback type currently requires a new connection and a republished workflow.

## Check the result and troubleshoot

In Studio, open **Run & inspect** to see the run and each step's status. A successful callback action shows a delivered result. If your app needs delivery details, use the workspace-scoped callbacks endpoint above.

| Response or symptom | Check |
| --- | --- |
| `202` with `run_ids: []` | Publish the workflow and bind its app event type. |
| `401` | Check the correct inbound secret, raw-body signature, and clock. Timestamps must be within five minutes. |
| `404` | Check the webhook URL; a revoked connection cannot receive new events. |
| `409` | Do not reuse a `source` and event `id` with different body bytes. A rotation during intake can also return `409`. |
| `413`, `415`, or `422` | Keep the request at or under 16,000 bytes, use `application/cloudevents+json`, and match the registered event schema. |
| `429` | The connection's event rate or the workspace's active-run limit was reached; retry later using the same event ID and body. |
| Callback failed | Check your public HTTPS endpoint, TLS certificate, response status, and the callback delivery status. Automiq does not follow redirects. |

Owners can use **Rotate** to replace both secrets or **Revoke** to stop the connection. Coordinate rotation with your app deployment, because there is no overlap window for old secrets.

**Current limit:** Automiq does not yet track a submitted GitHub PR until merge or automatically close a task. That flow needs the planned generic event correlation and PR-status integration. The app event and callback contract above is available now.
