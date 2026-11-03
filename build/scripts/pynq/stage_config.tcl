proc sr_lut_env_or_default {name default_value} {
  if {[info exists ::env($name)] && $::env($name) ne ""} {
    return $::env($name)
  }
  return $default_value
}

proc sr_lut_resolve_path {root_dir value} {
  if {[file pathtype $value] eq "absolute"} {
    return [file normalize $value]
  }
  return [file normalize [file join $root_dir $value]]
}

proc sr_lut_stage_config {project_root build_kind} {
  set stage_number [sr_lut_env_or_default SR_LUT_STAGE_NUMBER 33]
  if {![string is integer -strict $stage_number] || $stage_number < 1} {
    error "SR_LUT_STAGE_NUMBER must be a positive integer"
  }

  if {$stage_number == 33} {
    set default_results compatibility_stage33
  } else {
    set default_results [file join local "stage_${stage_number}"]
  }
  set results_folder [sr_lut_env_or_default \
      SR_LUT_RESULTS_FOLDER $default_results]

  if {$build_kind eq "dynamic_system"} {
    set default_build "stage_${stage_number}_x4_dynamic_system"
  } else {
    error "Unsupported SR-LUT build kind: $build_kind"
  }
  set build_label [sr_lut_env_or_default SR_LUT_BUILD_LABEL $default_build]
  set build_dir [sr_lut_resolve_path $project_root \
      [file join build $build_label]]

  set default_scheduler [file join src/rtl sr_lut_rgb_scheduler.sv]
  set scheduler_file [sr_lut_resolve_path $project_root \
      [sr_lut_env_or_default SR_LUT_SCHEDULER_FILE $default_scheduler]]

  set default_overlay [file join build $build_label export]
  set overlay_dir [sr_lut_resolve_path $project_root \
      [sr_lut_env_or_default SR_LUT_OVERLAY_DIR $default_overlay]]

  set minimum_setup_wns [sr_lut_env_or_default SR_LUT_MIN_SETUP_WNS 0.0]
  if {![string is double -strict $minimum_setup_wns]} {
    error "SR_LUT_MIN_SETUP_WNS must be numeric"
  }
  set default_strategy ""
  if {$build_kind eq "dynamic_system"} {
    set default_strategy Performance_ExplorePostRoutePhysOpt
  }
  set implementation_strategy [sr_lut_env_or_default \
      SR_LUT_IMPL_STRATEGY $default_strategy]

  set wrapper_variant [sr_lut_env_or_default SR_LUT_WRAPPER_VARIANT dynamic]
  if {$wrapper_variant ni {dynamic video420 mjpeg}} {
    error "SR_LUT_WRAPPER_VARIANT must be dynamic, video420 or mjpeg"
  }
  set s2mm_port [string tolower [sr_lut_env_or_default SR_LUT_S2MM_PORT hp0]]
  if {$s2mm_port ni {hp0 hp1 acp}} {
    error "SR_LUT_S2MM_PORT must be hp0, hp1 or acp"
  }
  set mode_gpio_enabled [sr_lut_env_or_default SR_LUT_MODE_GPIO 0]
  if {$mode_gpio_enabled ni {0 1}} {
    error "SR_LUT_MODE_GPIO must be 0 or 1"
  }
  set scale_gpio_enabled [sr_lut_env_or_default SR_LUT_SCALE_GPIO 0]
  if {$scale_gpio_enabled ni {0 1}} {
    error "SR_LUT_SCALE_GPIO must be 0 or 1"
  }
  set scale [sr_lut_env_or_default SR_LUT_SCALE 4]
  set sample_bits [sr_lut_env_or_default SR_LUT_SAMPLE_BITS 5]
  set q [sr_lut_env_or_default SR_LUT_Q 32]
  set levels [sr_lut_env_or_default SR_LUT_LEVELS 9]
  set lut_depth [sr_lut_env_or_default SR_LUT_LUT_DEPTH 6561]
  set addr_bits [sr_lut_env_or_default SR_LUT_ADDR_BITS 13]
  set scheduler_cores [sr_lut_env_or_default SR_LUT_SCHEDULER_CORES 3]
  set lut_mem_rel [sr_lut_env_or_default SR_LUT_MEM_FILE \
      [file join data/lut bram_mem model_s_x4_5bit.mem]]
  foreach pair [list [list scale $scale] [list sample_bits $sample_bits] \
      [list q $q] [list levels $levels] [list lut_depth $lut_depth] \
      [list addr_bits $addr_bits] [list scheduler_cores $scheduler_cores]] {
    set key [lindex $pair 0]
    set value [lindex $pair 1]
    if {![string is integer -strict $value] || $value < 1} {
      error "SR_LUT_[string toupper $key] must be a positive integer"
    }
  }
  return [dict create \
      stage_number $stage_number \
      results_folder $results_folder \
      build_dir $build_dir \
      scheduler_file $scheduler_file \
      overlay_dir $overlay_dir \
      minimum_setup_wns $minimum_setup_wns \
      wrapper_variant $wrapper_variant \
      s2mm_port $s2mm_port \
      mode_gpio_enabled $mode_gpio_enabled \
      scale_gpio_enabled $scale_gpio_enabled \
      implementation_strategy $implementation_strategy \
      scale $scale sample_bits $sample_bits q $q levels $levels \
      lut_depth $lut_depth addr_bits $addr_bits scheduler_cores $scheduler_cores \
      lut_mem_rel $lut_mem_rel]
}
