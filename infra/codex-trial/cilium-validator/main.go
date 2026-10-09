// Offline regression using the upstream operator/agent sanitizer, not a copy.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime/debug"

	"github.com/cilium/cilium/pkg/option"
	"github.com/cilium/cilium/pkg/policy/api"
)

type scenario struct {
	Name          string          `json:"name"`
	Rules         json.RawMessage `json:"rules"`
	ExpectedError string          `json:"expectedError"`
}

func check() error {
	build, ok := debug.ReadBuildInfo()
	pinned := false
	if ok {
		for _, dependency := range build.Deps {
			if dependency.Path == "github.com/cilium/cilium" {
				pinned = dependency.Version == "v1.20.2" && dependency.Replace == nil
			}
		}
	}
	if !pinned || len(os.Args) != 2 {
		return fmt.Errorf("pinned upstream version and case file required")
	}
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		return err
	}
	var cases []scenario
	if err = json.Unmarshal(raw, &cases); err != nil {
		return err
	}
	if len(cases) == 0 {
		return fmt.Errorf("nonempty scenarios required")
	}
	passed := 0
	for _, defaults := range []bool{false, true} {
		option.Config.EnableNonDefaultDenyPolicies = defaults
		for _, test := range cases {
			var rules api.Rules
			if err = json.Unmarshal(test.Rules, &rules); err != nil {
				return err
			}
			if len(rules) == 0 {
				return fmt.Errorf("nonempty rules required")
			}
			observed := ""
			for _, rule := range rules {
				if rule == nil {
					return fmt.Errorf("null rule")
				}
				if err = rule.Sanitize(); err != nil {
					observed = err.Error()
					break
				}
			}
			if observed != test.ExpectedError {
				return fmt.Errorf("%s defaultFlag=%t got=%q expected=%q", test.Name, defaults, observed, test.ExpectedError)
			}
			record := map[string]any{"name": test.Name, "nonDefaultDenyFlag": defaults,
				"rules": len(rules), "expectedRejected": observed != "", "upstream": "cilium/v1.20.2", "ok": true}
			encoded, _ := json.Marshal(record)
			fmt.Println(string(encoded))
			passed++
		}
	}
	fmt.Printf("Cilium1.20.2 actual Rule.Sanitize: %d cases passed; no agent/cluster/network execution\n", passed)
	return nil
}

func main() {
	if err := check(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
