# Allow the intentional combinational loop of the ring oscillator. Applied in
# implementation only (nets exist post-synth); the DONT_TOUCH LUTs keep the ring.
set_property ALLOW_COMBINATORIAL_LOOPS TRUE [get_nets -hier -filter {NAME =~ *ro_tune_0*}]
