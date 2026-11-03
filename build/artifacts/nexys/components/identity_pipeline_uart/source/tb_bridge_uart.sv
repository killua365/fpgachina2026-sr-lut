`timescale 1ns/1ps
module tb_bridge_uart;
    reg clk=0;always #5 clk=~clk;
    reg [1279:0] payload=0;
    wire tx;
    bridge_uart #(.CLOCK_HZ(1000000),.BAUD(100000),.INTERVAL(20000)) dut(.*);
    integer i,k,frame,fd;
    reg [7:0] data;
    initial begin
        for(i=0;i<160;i=i+1) payload[i*8 +: 8]=i;
        fd=$fopen("uart.hex","w");
        for(frame=0;frame<2;frame=frame+1) begin
            for(i=0;i<172;i=i+1) begin
                @(negedge tx);#150;
                for(k=0;k<8;k=k+1) begin data[k]=tx;#100;end
                if(tx!==1'b1) $fatal(1,"UART stop bit");
                $fwrite(fd,"%02x",data);
                if(frame==0 && i==0) payload={1280{1'b1}};
            end
            $fwrite(fd,"\n");
        end
        $fclose(fd);$display("PASS: UART 2 complete frames");$finish;
    end
    initial begin #800000;$fatal(1,"UART bench timeout");end
endmodule
