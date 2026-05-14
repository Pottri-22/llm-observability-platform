{{/*
Week 0 · Block 7 — named-template partials.
Not a manifest itself: it defines reusable snippets the other 4 templates
`include`. Centralising names + labels here is what stops a chart from
drifting (a Service selector that no longer matches the Deployment, etc.).
*/}}

{{/* Chart base name, overridable. */}}
{{- define "hello-aegis.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully-qualified app name. Defaults to "<release>-<chart>", but collapses to
just "<release>" when the release name already contains the chart name (so a
`helm install hello-aegis ./hello-aegis` gives clean "hello-aegis", not
"hello-aegis-hello-aegis").
*/}}
{{- define "hello-aegis.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Full label set — goes on every object's metadata.labels. */}}
{{- define "hello-aegis.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "hello-aegis.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels — the STABLE subset. These go in Deployment.spec.selector
and Service.spec.selector and must never change for a release, or the
Service stops finding its pods. Kept separate from the full label set on
purpose: version/chart labels churn, selectors must not.
*/}}
{{- define "hello-aegis.selectorLabels" -}}
app.kubernetes.io/name: {{ include "hello-aegis.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* ServiceAccount name — generated when create=true, else the given/"default". */}}
{{- define "hello-aegis.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "hello-aegis.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
