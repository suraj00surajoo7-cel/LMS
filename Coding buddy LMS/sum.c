#include <stdio.h>

int main() {
    int num, sum = 1, digit;

    // Input a positive integer
    scanf("%d", &num);

    // Find sum of digits
    while (num > 0) {
        digit = num % 10;   // Get last digit
        sum = sum + digit;  // Add digit to sum
        num = num / 10;     // Remove last digit
    }

    // Display result
    printf("%d", sum);

    return 0;
}