`timescale 1ns/1ps

module tb_sr_lut_rom;

  import sr_lut_pkg::*;

  localparam int unsigned MAX_PRINTED_ERRORS = 20;

  logic clk;

  logic x2_enable_a;
  logic x2_enable_b;
  logic [X2_ADDR_BITS - 1:0] x2_address_a;
  logic [X2_ADDR_BITS - 1:0] x2_address_b;
  logic [X2_LUT_WORD_BITS - 1:0] x2_data_a;
  logic [X2_LUT_WORD_BITS - 1:0] x2_data_b;

  logic x4_enable_a;
  logic x4_enable_b;
  logic [X4_ADDR_BITS - 1:0] x4_address_a;
  logic [X4_ADDR_BITS - 1:0] x4_address_b;
  logic [X4_LUT_WORD_BITS - 1:0] x4_data_a;
  logic [X4_LUT_WORD_BITS - 1:0] x4_data_b;

  logic [X2_LUT_WORD_BITS - 1:0] x2_expected [0:X2_LUT_DEPTH - 1];
  logic [X4_LUT_WORD_BITS - 1:0] x4_expected [0:X4_LUT_DEPTH - 1];

  string lut_root;
  integer error_count;
  integer word_check_count;

  sr_lut_rom #(
    .DEPTH(X2_LUT_DEPTH),
    .ADDR_BITS(X2_ADDR_BITS),
    .WORD_BITS(X2_LUT_WORD_BITS),
    .MEM_FILE("data/lut/bram_mem/model_s_x2_4bit.mem")
  ) dut_x2 (
    .clk_i(clk),
    .read_enable_a_i(x2_enable_a),
    .read_address_a_i(x2_address_a),
    .read_data_a_o(x2_data_a),
    .read_enable_b_i(x2_enable_b),
    .read_address_b_i(x2_address_b),
    .read_data_b_o(x2_data_b)
  );

  sr_lut_rom #(
    .DEPTH(X4_LUT_DEPTH),
    .ADDR_BITS(X4_ADDR_BITS),
    .WORD_BITS(X4_LUT_WORD_BITS),
    .MEM_FILE("data/lut/bram_mem/model_s_x4_5bit.mem")
  ) dut_x4 (
    .clk_i(clk),
    .read_enable_a_i(x4_enable_a),
    .read_address_a_i(x4_address_a),
    .read_data_a_o(x4_data_a),
    .read_enable_b_i(x4_enable_b),
    .read_address_b_i(x4_address_b),
    .read_data_b_o(x4_data_b)
  );

  always #5 clk = ~clk;

  task automatic record_x2_error(
    input integer address,
    input logic [X2_LUT_WORD_BITS - 1:0] expected_word,
    input logic [X2_LUT_WORD_BITS - 1:0] actual_word
  );
    begin
      error_count = error_count + 1;
      if (error_count <= MAX_PRINTED_ERRORS) begin
        $display(
          "ERROR config=x2_4bit address=%0d expected=%08h actual=%08h",
          address, expected_word, actual_word
        );
      end
    end
  endtask

  task automatic record_x4_error(
    input integer address,
    input logic [X4_LUT_WORD_BITS - 1:0] expected_word,
    input logic [X4_LUT_WORD_BITS - 1:0] actual_word
  );
    begin
      error_count = error_count + 1;
      if (error_count <= MAX_PRINTED_ERRORS) begin
        $display(
          "ERROR config=x4_5bit address=%0d expected=%032h actual=%032h",
          address, expected_word, actual_word
        );
      end
    end
  endtask

  task automatic run_x2_tests;
    integer address;
    begin
      $display("E4 ROM x2_4bit: checking all %0d words", X2_LUT_DEPTH);
      $readmemh($sformatf("%s/model_s_x2_4bit.mem", lut_root), x2_expected);

      for (address = 0; address < X2_LUT_DEPTH; address = address + 2) begin
        @(negedge clk);
        x2_enable_a = 1'b1;
        x2_address_a = address[X2_ADDR_BITS - 1:0];
        if ((address + 1) < X2_LUT_DEPTH) begin
          x2_enable_b = 1'b1;
          x2_address_b = (address + 1);
        end else begin
          x2_enable_b = 1'b0;
          x2_address_b = '0;
        end

        @(negedge clk);
        word_check_count = word_check_count + 1;
        if (x2_data_a !== x2_expected[address]) begin
          record_x2_error(address, x2_expected[address], x2_data_a);
        end
        if ((address + 1) < X2_LUT_DEPTH) begin
          word_check_count = word_check_count + 1;
          if (x2_data_b !== x2_expected[address + 1]) begin
            record_x2_error(address + 1, x2_expected[address + 1], x2_data_b);
          end
        end
      end

      x2_enable_a = 1'b0;
      x2_enable_b = 1'b0;
    end
  endtask

  task automatic run_x4_tests;
    integer address;
    begin
      $display("E4 ROM x4_5bit: checking all %0d words", X4_LUT_DEPTH);
      $readmemh($sformatf("%s/model_s_x4_5bit.mem", lut_root), x4_expected);

      for (address = 0; address < X4_LUT_DEPTH; address = address + 2) begin
        @(negedge clk);
        x4_enable_a = 1'b1;
        x4_address_a = address[X4_ADDR_BITS - 1:0];
        if ((address + 1) < X4_LUT_DEPTH) begin
          x4_enable_b = 1'b1;
          x4_address_b = (address + 1);
        end else begin
          x4_enable_b = 1'b0;
          x4_address_b = '0;
        end

        @(negedge clk);
        word_check_count = word_check_count + 1;
        if (x4_data_a !== x4_expected[address]) begin
          record_x4_error(address, x4_expected[address], x4_data_a);
        end
        if ((address + 1) < X4_LUT_DEPTH) begin
          word_check_count = word_check_count + 1;
          if (x4_data_b !== x4_expected[address + 1]) begin
            record_x4_error(address + 1, x4_expected[address + 1], x4_data_b);
          end
        end
      end

      x4_enable_a = 1'b0;
      x4_enable_b = 1'b0;
    end
  endtask

  initial begin
    clk = 1'b0;
    error_count = 0;
    word_check_count = 0;
    x2_enable_a = 1'b0;
    x2_enable_b = 1'b0;
    x2_address_a = '0;
    x2_address_b = '0;
    x4_enable_a = 1'b0;
    x4_enable_b = 1'b0;
    x4_address_a = '0;
    x4_address_b = '0;

    if (!$value$plusargs("LUT_ROOT=%s", lut_root)) begin
      lut_root = "data/lut/bram_mem";
    end

    run_x2_tests();
    run_x4_tests();

    if (error_count != 0) begin
      $fatal(
        1,
        "E4_ROM_FAIL errors=%0d word_checks=%0d",
        error_count,
        word_check_count
      );
    end

    $display(
      "E4_ROM_PASS x2_words=%0d x4_words=%0d total_word_checks=%0d",
      X2_LUT_DEPTH,
      X4_LUT_DEPTH,
      word_check_count
    );
    $finish;
  end

endmodule
