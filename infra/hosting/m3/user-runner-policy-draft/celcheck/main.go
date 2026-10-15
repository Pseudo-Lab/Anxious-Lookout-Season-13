// Offline Kubernetes 1.36 typed CEL harness; never connects to a cluster.
package main

import (
	"encoding/json"
	"fmt"
	"github.com/google/cel-go/cel"
	"github.com/google/cel-go/common/types"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/util/version"
	apicel "k8s.io/apiserver/pkg/cel"
	"k8s.io/apiserver/pkg/cel/environment"
	"k8s.io/apiserver/pkg/cel/openapi"
	"k8s.io/kube-openapi/pkg/validation/spec"
	"os"
)

func check(err error) {
	if err != nil {
		panic(err)
	}
}
func read(path string, out any) { b, e := os.ReadFile(path); check(e); check(json.Unmarshal(b, out)) }
func expand(v any, defs map[string]any) any {
	switch x := v.(type) {
	case map[string]any:
		if r, ok := x["$ref"].(string); ok {
			return expand(defs[r[len("#/definitions/"):]], defs)
		}
		out := map[string]any{}
		for k, c := range x {
			out[k] = expand(c, defs)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, c := range x {
			out[i] = expand(c, defs)
		}
		return out
	default:
		return v
	}
}
func clone(x map[string]any) map[string]any {
	b, e := json.Marshal(x)
	check(e)
	var y map[string]any
	check(json.Unmarshal(b, &y))
	return y
}
func set(x any, path []any, value any) {
	for i, key := range path {
		last := i == len(path)-1
		switch k := key.(type) {
		case string:
			m := x.(map[string]any)
			if last {
				m[k] = value
				return
			}
			x = m[k]
		case float64:
			a := x.([]any)
			if last {
				a[int(k)] = value
				return
			}
			x = a[int(k)]
		}
	}
}
func main() {
	if len(os.Args) != 3 {
		panic("usage: celcheck inventory.json suite.json")
	}
	var inventory struct{ Definitions map[string]any }
	read(os.Args[1], &inventory)
	var suite struct {
		Positive    map[string]any
		Validations []struct {
			Expression string
			Message    string
		}
		Cases []struct {
			Validation int
			Path       []any
			Bad        any
		}
		LiteralProbes []struct {
			Expression string
			Expected   string
		}
	}
	read(os.Args[2], &suite)
	root := map[string]any{"type": "object", "properties": map[string]any{
		"apiVersion": map[string]any{"type": "string"}, "kind": map[string]any{"type": "string"},
		"metadata": expand(inventory.Definitions["io.k8s.apimachinery.pkg.apis.meta.v1.ObjectMeta"], inventory.Definitions),
		"spec":     expand(inventory.Definitions["io.k8s.api.core.v1.PodSpec"], inventory.Definitions),
	}}
	b, e := json.Marshal(root)
	check(e)
	var schema spec.Schema
	check(json.Unmarshal(b, &schema))
	decl := openapi.SchemaDeclType(&schema, true).MaybeAssignTypeName("Pod")
	// Request fields used by this policy only, explicitly typed (not dyn).
	reqSchema := spec.Schema{SchemaProps: spec.SchemaProps{Type: []string{"object"}, Properties: map[string]spec.Schema{
		"operation": *spec.StringProperty(),
		"userInfo":  {SchemaProps: spec.SchemaProps{Type: []string{"object"}, Properties: map[string]spec.Schema{"username": *spec.StringProperty()}}},
	}}}
	reqDecl := openapi.SchemaDeclType(&reqSchema, false).MaybeAssignTypeName("Request")
	envset, e := environment.MustBaseEnvSet(version.MajorMinor(1, 36)).Extend(environment.VersionedOptions{
		IntroducedVersion: version.MajorMinor(1, 36),
		DeclTypes:         []*apicel.DeclType{decl, reqDecl},
		EnvOptions:        []cel.EnvOption{cel.Variable("object", decl.CelType()), cel.Variable("request", reqDecl.CelType())},
	})
	check(e)
	env := envset.NewExpressionsEnv()
	for _, probe := range suite.LiteralProbes {
		ast, issues := env.Compile(probe.Expression)
		check(issues.Err())
		p, e := env.Program(ast)
		check(e)
		v, _, e := p.Eval(map[string]any{})
		check(e)
		if v != types.String(probe.Expected) {
			panic("literal escaping mismatch")
		}
	}
	// Prove this is typed: unknown fields must fail compilation.
	_, issues := env.Compile("object.spec.nonexistentField == true")
	if issues.Err() == nil {
		panic("untyped environment")
	}
	request := map[string]any{"operation": "CREATE", "userInfo": map[string]any{"username": "system:serviceaccount:codex-runner-rehearsal:runner-controller"}}
	nativeBytes, e := json.Marshal(suite.Positive)
	check(e)
	var native corev1.Pod
	check(json.Unmarshal(nativeBytes, &native))
	nativeBytes, e = json.Marshal(native)
	check(e)
	var serialized map[string]any
	check(json.Unmarshal(nativeBytes, &serialized))
	programs := make([]cel.Program, len(suite.Validations))
	for i, v := range suite.Validations {
		ast, issues := env.Compile(v.Expression)
		if issues.Err() != nil {
			panic(fmt.Sprintf("%d %s: %v", i, v.Message, issues.Err()))
		}
		if ast.OutputType() != cel.BoolType {
			panic("non-bool validation")
		}
		p, e := env.Program(ast, cel.CostLimit(100000))
		check(e)
		programs[i] = p
		result, _, e := p.Eval(map[string]any{"object": openapi.UnstructuredToVal(integers(suite.Positive), &schema), "request": openapi.UnstructuredToVal(request, &reqSchema)})
		if e != nil || result != types.True {
			panic(fmt.Sprintf("positive %d %s: %v %v", i, v.Message, result, e))
		}
	}
	for i, p := range programs {
		result, _, e := p.Eval(map[string]any{"object": openapi.UnstructuredToVal(integers(serialized), &schema), "request": openapi.UnstructuredToVal(request, &reqSchema)})
		if e != nil || result != types.True {
			panic(fmt.Sprintf("native serialized positive %d: %v %v", i, result, e))
		}
	}
	for _, c := range suite.Cases {
		obj := clone(suite.Positive)
		req := clone(request)
		if c.Path == nil {
			req["operation"] = "UPDATE"
		} else {
			set(obj, c.Path, c.Bad)
		}
		result, _, e := programs[c.Validation].Eval(map[string]any{"object": openapi.UnstructuredToVal(integers(obj), &schema), "request": openapi.UnstructuredToVal(req, &reqSchema)})
		if e != nil || result != types.False {
			panic(fmt.Sprintf("negative %d: %v %v", c.Validation, result, e))
		}
	}
	wrongCaller := clone(request)
	wrongCaller["userInfo"] = map[string]any{"username": "system:serviceaccount:other:runner-controller"}
	result, _, e := programs[0].Eval(map[string]any{"object": openapi.UnstructuredToVal(integers(suite.Positive), &schema), "request": openapi.UnstructuredToVal(wrongCaller, &reqSchema)})
	if e != nil || result != types.False {
		panic("wrong caller accepted or evaluation error")
	}
	if len(suite.LiteralProbes) != 5 {
		panic("literal probes missing")
	}
	fmt.Printf("PASS %d typed boolean expressions; %d positives (synthetic + native JSON), %d negatives, %d literal probes; unknown-field compile rejection; gate excluded\n", len(programs), 2*len(programs), len(suite.Cases)+1, len(suite.LiteralProbes))
}

// Kubernetes unstructured JSON numbers use int64 for integral values.
func integers(value any) any {
	switch x := value.(type) {
	case float64:
		if x == float64(int64(x)) {
			return int64(x)
		}
		return x
	case map[string]any:
		out := map[string]any{}
		for k, v := range x {
			out[k] = integers(v)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, v := range x {
			out[i] = integers(v)
		}
		return out
	default:
		return value
	}
}
